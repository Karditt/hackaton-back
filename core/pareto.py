"""Фронт Парето «срок — ресурс» (E12, заказчик №23, №52).

Два режима оптимизации дают два края: план «минимум срока» — самый ранний
конец работ при наибольшем ресурсе, план «минимум ресурса» без срока — самый
экономный и самый долгий. Между ними заказчик выбирает сам: «бывает, что стоят
жёсткие сроки и надо привлечь много дронов» (№52). Промежуточные точки —
режим «минимум ресурса» с ограничением по сроку, равномерно между краями
(метод ε-ограничения): каждая — настоящий план с краш-тестом.

Свёртка α·T + (1−α)·R (`Scenario.alpha`) для этого не годится: при
невыпуклом фронте она находит только его углы, а срок и ресурс у нас в одних
единицах, но разного смысла. Ограничение по сроку даёт ровно тот вопрос,
который задаёт заказчик: «сколько ресурса стоит закончить к такому-то сроку».

Точка, которую другая точка не хуже по обоим критериям и лучше хотя бы по
одному, помечается как доминируемая: эвристика не гарантирует, что план со
сроком позже обходится дешевле.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from core import assumptions, text
from core.catalog import VehicleCatalog
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import OptimizationMode, Scenario


@dataclass(frozen=True)
class ParetoPoint:
    """Один план фронта."""

    label: str
    """Что это за план — для человека."""
    mode: OptimizationMode
    deadline_s: float | None
    """Ограничение по сроку, секунды от начала работ; None — без срока."""
    feasible: bool
    makespan_s: float | None
    """Срок плана, секунды; None — план невыполним."""
    resource_s: float | None
    """Ресурс плана: налёт + циклы, секунды."""
    sortie_count: int | None
    vehicle_count: int | None
    elapsed_s: float
    """Время расчёта, секунды."""
    dominated: bool = False


def _plan(
    scenario: Scenario,
    catalog: VehicleCatalog | None,
    mode: OptimizationMode,
    deadline_s: float | None,
    label: str,
) -> tuple[ParetoPoint, PlanningRun]:
    """Прогнать конвейер в режиме `mode` со сроком `deadline_s` и свести в точку."""
    started = time.perf_counter()
    run = run_pipeline(
        scenario.model_copy(update={"deadline_s": deadline_s}), catalog, mode=mode, verify=False
    )
    metrics = run.mission.metrics if run.mission.is_feasible() else None
    point = ParetoPoint(
        label=label,
        mode=mode,
        deadline_s=deadline_s,
        feasible=metrics is not None,
        makespan_s=metrics.makespan_s if metrics else None,
        resource_s=metrics.resource_s if metrics else None,
        sortie_count=metrics.sortie_count if metrics else None,
        vehicle_count=metrics.vehicle_count if metrics else None,
        elapsed_s=time.perf_counter() - started,
    )
    return point, run


def _mark_dominated(points: list[ParetoPoint]) -> list[ParetoPoint]:
    """Пометить точки, которые другая выполнимая точка не хуже по обоим критериям."""
    scores = [
        (point.makespan_s, point.resource_s)
        for point in points
        if point.makespan_s is not None and point.resource_s is not None
    ]

    def dominated(point: ParetoPoint) -> bool:
        if point.makespan_s is None or point.resource_s is None:
            return False
        own = (point.makespan_s, point.resource_s)
        return any(t <= own[0] and r <= own[1] and (t, r) != own for t, r in scores)

    return [replace(point, dominated=dominated(point)) for point in points]


def pareto_front(
    scenario: Scenario,
    catalog: VehicleCatalog | None = None,
    steps: int = assumptions.PARETO_STEPS,
    on_point: Callable[[ParetoPoint], None] | None = None,
    on_plan: Callable[[ParetoPoint, PlanningRun], None] | None = None,
) -> list[ParetoPoint]:
    """Планы от «минимум срока» до «минимум ресурса» с ограничением по сроку между ними.

    Срок сценария не учитывается: фронт показывает, что стоит каждый срок.

    Args:
        scenario: сценарий; его флот — пул.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        steps: сколько промежуточных сроков между краями.
        on_point: вызывается после каждого плана — для прогресса.
        on_plan: вызывается после каждого плана вместе с самим планом — чтобы
            его можно было открыть и выгрузить (API заводит его задачей).

    Returns:
        Точки по возрастанию срока ограничения: «минимум срока», промежуточные,
        «минимум ресурса». Если краёв нет (задание невыполнимо даже без
        срока), — только они.
    """

    def done(planned: tuple[ParetoPoint, PlanningRun]) -> ParetoPoint:
        point, run = planned
        if on_point is not None:
            on_point(point)
        if on_plan is not None:
            on_plan(point, run)
        return point

    fastest = done(_plan(scenario, catalog, OptimizationMode.MIN_MAKESPAN, None, "минимум срока"))
    frugal = done(_plan(scenario, catalog, OptimizationMode.MIN_RESOURCE, None, "минимум ресурса"))
    points = [fastest]
    if fastest.makespan_s is not None and frugal.makespan_s is not None:
        low, high = fastest.makespan_s, frugal.makespan_s
        for index in range(1, steps + 1):
            deadline_s = low + (high - low) * index / (steps + 1)
            if deadline_s <= low:
                continue
            points.append(
                done(
                    _plan(
                        scenario,
                        catalog,
                        OptimizationMode.MIN_RESOURCE,
                        deadline_s,
                        f"минимум ресурса к сроку {text.num(deadline_s / 3600, 2)} ч",
                    )
                )
            )
    points.append(frugal)
    return _mark_dominated(points)


__all__ = ["ParetoPoint", "pareto_front"]
