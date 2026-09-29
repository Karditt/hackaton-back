"""Подбор места новой площадки (E15, долг №60, заказчик №28).

Заказчик: площадки задаёт пользователь, но «если сервис будет подбирать —
будет вообще шикарно» (№28). Здесь — два подбора. `place_sites` ставит
площадки сценарию, в котором их нет вовсе (новый сценарий из слоёв KML).
`suggest_sites` предлагает одну дополнительную площадку для выполнимого или
невыполнимого задания, с проверкой расчётом.

Площадки нового сценария. Область заменяется точками сетки; площадки
ставятся жадно — каждая следующая у самой дальней от уже поставленных точки,
пока до любой точки дальше `AUTO_SITE_RADIUS_M` и площадок меньше
`AUTO_SITES_MAX`; места уточняются: точки делятся по ближайшей площадке,
площадка встаёт в центр наименьшего круга своих точек. Место в запретной
зоне, у препятствия или вне пространства заменяется ближайшей свободной
точкой области.

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

import numpy as np
import shapely
from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.advise import site_spot
from core.catalog import VehicleCatalog
from core.geo.mask import WorkArea
from core.geo.project import polygon_to_shapely, polygons_of
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import Cell, GeoPoint, MetricPoint, Scenario, Site, SiteKind

#: Идентификатор предложенной площадки в пробном сценарии.
SUGGESTED_SITE_ID = "suggested-site"

#: Префикс идентификаторов площадок, подобранных новому сценарию.
AUTO_SITE_PREFIX = "auto-site"

#: Имена подобранных площадок по стороне от центра области: с севера по часовой.
_COMPASS_NAMES = (
    "Северная",
    "Северо-восточная",
    "Восточная",
    "Юго-восточная",
    "Южная",
    "Юго-западная",
    "Западная",
    "Северо-западная",
)


def _area_samples(region: BaseGeometry, count: int) -> np.ndarray:
    """Точки области: сетка примерно из `count` точек и по точке на каждый кусок.

    Returns:
        Массив (n, 2) координат, метры.
    """
    step = math.sqrt(region.area / count)
    minx, miny, maxx, maxy = region.bounds
    grid_x, grid_y = np.meshgrid(
        np.arange(minx + step / 2.0, maxx, step), np.arange(miny + step / 2.0, maxy, step)
    )
    xs, ys = grid_x.ravel(), grid_y.ravel()
    shapely.prepare(region)
    inside = shapely.contains_xy(region, xs, ys)
    pieces = [part.representative_point() for part in polygons_of(region)]
    extra = np.array([(point.x, point.y) for point in pieces]).reshape(-1, 2)
    return np.vstack([np.column_stack([xs[inside], ys[inside]]), extra])


def _circle_center(points: np.ndarray) -> np.ndarray:
    """Центр наименьшего круга вокруг точек, метры."""
    if len(points) == 1:
        return points[0]
    center = shapely.minimum_bounding_circle(MultiPoint(points)).centroid
    return np.array([center.x, center.y])


def _nearest(points: np.ndarray, centers: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Для каждой точки — номер ближайшего центра и расстояние до него, метры."""
    distances = np.stack([np.hypot(*(points - center).T) for center in centers], axis=1)
    return distances.argmin(axis=1), distances.min(axis=1)


def _free_spot(work_area: WorkArea, center: np.ndarray, own: np.ndarray) -> np.ndarray:
    """Место площадки не в запретном: центр или ближайшая к нему свободная точка своих.

    Запретное — как у совета «площадка» (`site_spot`): зоны, закрывающие хоть
    часть коридора высот, препятствия с буфером и всё вне границы
    пространства. Свободной нет — центр как есть: площадку в зоне назовёт
    проверка входа (`build_work_area`).
    """
    blocked = unary_union(
        [
            zone.buffered
            for zone in work_area.zones
            if zone.blocks_altitude(0.0, work_area.corridor_max_m)
        ]
        + [item.buffered for item in work_area.obstacles]
    )

    def free(x: float, y: float) -> bool:
        point = Point(x, y)
        inside = work_area.airspace is None or work_area.airspace.contains(point)
        return inside and not blocked.intersects(point)

    if free(*center):
        return center
    order = np.argsort(np.hypot(*(own - center).T), kind="stable")
    for index in order:
        if free(*own[index]):
            return own[index]
    return center


def _compass_name(center: np.ndarray, anchor: np.ndarray) -> str:
    """Сторона площадки от центра области: «Северная», «Юго-западная»…"""
    east, north = center - anchor
    bearing = math.degrees(math.atan2(east, north)) % 360.0
    sector = 360.0 / len(_COMPASS_NAMES)
    return _COMPASS_NAMES[int((bearing + sector / 2.0) // sector) % len(_COMPASS_NAMES)]


def place_sites(
    work_area: WorkArea,
    radius_m: float = assumptions.AUTO_SITE_RADIUS_M,
    max_sites: int = assumptions.AUTO_SITES_MAX,
) -> list[Site]:
    """Поставить площадки взлёта-посадки сценарию, в котором их нет (№28).

    Args:
        work_area: рабочая область сценария (метры); её площадки не читаются.
        radius_m: радиус работы вокруг площадки, метры.
        max_sites: сколько площадок самое большее.

    Returns:
        Штатные площадки в WGS84: одна — «Центральная», несколько — по стороне
        от центра области («Северная», «Юго-западная»…). Детерминированно.
    """
    points = _area_samples(work_area.area, assumptions.AUTO_SITE_SAMPLES)
    centers = [_circle_center(points)]
    labels, distances = _nearest(points, centers)
    while len(centers) < max_sites and distances.max() > radius_m:
        centers.append(points[int(distances.argmax())])
        for _ in range(assumptions.AUTO_SITE_REFINE_ROUNDS):
            labels, _ = _nearest(points, centers)
            moved = [
                _circle_center(points[labels == index]) if np.any(labels == index) else center
                for index, center in enumerate(centers)
            ]
            if all(np.allclose(a, b) for a, b in zip(moved, centers, strict=True)):
                break
            centers = moved
        labels, distances = _nearest(points, centers)

    anchor = _circle_center(points)
    sites: list[Site] = []
    names: dict[str, int] = {}
    for index, center in enumerate(centers):
        spot = _free_spot(work_area, center, points[labels == index])
        name = "Центральная" if len(centers) == 1 else _compass_name(spot, anchor)
        names[name] = names.get(name, 0) + 1
        if names[name] > 1:
            name = f"{name} {names[name]}"
        sites.append(
            Site(
                id=f"{AUTO_SITE_PREFIX}-{index + 1}",
                name=name,
                position=work_area.projector.xy_to_geo(float(spot[0]), float(spot[1])),
                kind=SiteKind.PRIMARY,
            )
        )
    return sites


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


__all__ = [
    "AUTO_SITE_PREFIX",
    "SUGGESTED_SITE_ID",
    "SiteTrial",
    "place_sites",
    "site_candidates",
    "suggest_sites",
]
