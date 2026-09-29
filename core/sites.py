"""Подбор места новой площадки (E15, долг №60, заказчик №28).

Заказчик: площадки задаёт пользователь, но «если сервис будет подбирать —
будет вообще шикарно» (№28). Здесь — подбор одной дополнительной площадки
для выполнимого или невыполнимого задания, с проверкой расчётом.

Кандидаты. Подобласти упорядочиваются по расстоянию до ближайшей штатной
площадки. От самой дальней скопление наращивается ближайшими к нему
подобластями из дальних, пока не наберёт долю площади из
`SITE_SUGGEST_SHARES`: так кандидат обслуживает одну компактную дальнюю
группу, а не середину между двумя дальними краями. Место — центр
наименьшего круга вокруг скопления, если он не в запретном
(`core.advise.site_spot`).

Проверка. Каждый кандидат — полный расчёт сценария с этой площадкой. Приписка
бортов к площадкам снимается и в базовом расчёте, и в пробах: иначе новая
площадка осталась бы пустой, а выигрыш — неотличим от выигрыша за счёт
свободной расстановки. Ответ — срок и ресурс каждого кандидата против базового.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from shapely.ops import unary_union

from core import assumptions
from core.advise import site_spot
from core.catalog import VehicleCatalog
from core.geo.project import polygon_to_shapely
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import Cell, GeoPoint, MetricPoint, Scenario, Site, SiteKind

#: Идентификатор предложенной площадки в пробном сценарии.
SUGGESTED_SITE_ID = "suggested-site"


@dataclass(frozen=True)
class SiteTrial:
    """Расчёт сценария — базовый (без новой площадки) или с кандидатом."""

    label: str
    position: GeoPoint | None
    """Место новой площадки, WGS84; None — базовый расчёт."""
    share: float | None
    """Доля площади работы, ради которой поставлен кандидат."""
    feasible: bool
    makespan_s: float | None
    resource_s: float | None
    elapsed_s: float


def _distance(a: MetricPoint, b: MetricPoint) -> float:
    return math.hypot(a.x_m - b.x_m, a.y_m - b.y_m)


def site_candidates(
    cells: list[Cell],
    sites: list[MetricPoint],
    shares: tuple[float, ...] = assumptions.SITE_SUGGEST_SHARES,
) -> list[tuple[float, list[Cell]]]:
    """Скопления дальней работы — по одному на долю площади.

    Args:
        cells: подобласти.
        sites: штатные площадки, метры.
        shares: доли площади работы, 0..1.

    Returns:
        Пары «доля, подобласти скопления» — в порядке `shares`.
    """
    if not cells or not sites:
        return []
    far = sorted(
        cells, key=lambda cell: (-min(_distance(cell.centroid, site) for site in sites), cell.id)
    )
    total = sum(cell.area_m2 for cell in cells)
    seed = far[0].centroid
    # Дальние — дальше медианы; из них скопление растёт от самой дальней.
    pool = far[: max(1, len(far) // 2)]
    grown = sorted(pool, key=lambda cell: (_distance(cell.centroid, seed), cell.id))
    result: list[tuple[float, list[Cell]]] = []
    for share in shares:
        chosen: list[Cell] = []
        area = 0.0
        for cell in grown:
            chosen.append(cell)
            area += cell.area_m2
            if area >= share * total:
                break
        result.append((share, chosen))
    return result


def _unpinned(scenario: Scenario) -> Scenario:
    """Сценарий без приписки бортов к площадкам — площадки выбирает распределение."""
    fleet = [unit.model_copy(update={"site_id": None}) for unit in scenario.fleet]
    return scenario.model_copy(update={"fleet": fleet})


def _summary(
    run: PlanningRun, label: str, position: GeoPoint | None, share: float | None, started: float
) -> SiteTrial:
    """Итог расчёта пробы: срок и ресурс плана или невыполнимость."""
    metrics = run.mission.metrics if run.mission.is_feasible() else None
    return SiteTrial(
        label=label,
        position=position,
        share=share,
        feasible=metrics is not None,
        makespan_s=metrics.makespan_s if metrics else None,
        resource_s=metrics.resource_s if metrics else None,
        elapsed_s=time.perf_counter() - started,
    )


def _trial(
    scenario: Scenario,
    catalog: VehicleCatalog | None,
    label: str,
    position: GeoPoint | None,
    share: float | None,
) -> tuple[SiteTrial, PlanningRun]:
    started = time.perf_counter()
    run = run_pipeline(scenario, catalog, verify=False)
    return _summary(run, label, position, share, started), run


def suggest_sites(
    scenario: Scenario,
    catalog: VehicleCatalog | None = None,
    shares: tuple[float, ...] = assumptions.SITE_SUGGEST_SHARES,
    on_trial: Callable[[SiteTrial], None] | None = None,
    on_plan: Callable[[SiteTrial, PlanningRun], None] | None = None,
) -> list[SiteTrial]:
    """Предложить место новой площадки и проверить каждое расчётом.

    Args:
        scenario: сценарий; его пул и площадки.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        shares: доли дальней работы, под которые ставятся кандидаты.
        on_trial: вызывается после каждого расчёта — для прогресса.
        on_plan: вызывается после каждого расчёта вместе с планом — чтобы его
            можно было открыть и выгрузить (API заводит его задачей).

    Returns:
        Базовый расчёт (без новой площадки, без приписки бортов) и расчёты с
        каждым кандидатом. Если до подобластей не дошло — только базовый.
    """
    free = _unpinned(scenario)

    def done(planned: tuple[SiteTrial, PlanningRun]) -> SiteTrial:
        trial, run = planned
        if on_trial is not None:
            on_trial(trial)
        if on_plan is not None:
            on_plan(trial, run)
        return trial

    base = _trial(free, catalog, "как есть (площадки бортам выбирает расчёт)", None, None)
    base_run = base[1]
    trials = [done(base)]
    decomposition = base_run.result.decomposition
    if decomposition is None:
        return trials
    work_area = base_run.work_area
    sites = [work_area.site(site.id) for site in scenario.primary_sites()]
    seen: set[tuple[float, float]] = set()
    for share, cells in site_candidates(decomposition.cells, sites, shares):
        region = unary_union([polygon_to_shapely(cell.polygon) for cell in cells])
        spot = site_spot(work_area, region)
        key = (round(spot.x), round(spot.y))
        if key in seen:
            continue
        seen.add(key)
        position = work_area.projector.xy_to_geo(spot.x, spot.y)
        site = Site(
            id=SUGGESTED_SITE_ID,
            name="Предложенная площадка",
            position=position,
            kind=SiteKind.PRIMARY,
        )
        with_site = free.model_copy(update={"sites": [*free.sites, site]})
        trials.append(
            done(
                _trial(
                    with_site,
                    catalog,
                    f"площадка для дальних {share * 100:.0f}% площади",
                    position,
                    share,
                )
            )
        )
    return trials


__all__ = ["SUGGESTED_SITE_ID", "SiteTrial", "site_candidates", "suggest_sites"]
