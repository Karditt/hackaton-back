"""Критерии оптимизации (заказчик, №23; PROJECT_BRIEF.md §4.3).

ЗАФИКСИРОВАНЫ (AGENT.md §7). Изменение режимов, весов, слагаемых, нормировки
или стоимости цикла — только по явной задаче. Отдельный модуль ровно для
того, чтобы формулы не расползлись по оптимизатору копиями.

* T — срок: makespan, от начала работ до посадки последнего борта.
* R — ресурс: суммарный налёт плюс τ_цикл × число вылетов. Взлёт и посадка
  изнашивают технику сверх налёта (№23).

Режимы: «минимум срока» — минимизировать T; «минимум ресурса» —
минимизировать R при T ≤ дедлайн. Свёртка α·T + (1−α)·R — для фронта Парето.
"""

from __future__ import annotations

from core import assumptions
from core.schemas import OptimizationMode


def resource_s(total_flight_time_s: float, cycle_count: int) -> float:
    """Ресурс техники R, секунды налёта.

    Args:
        total_flight_time_s: суммарный налёт всех бортов, секунды.
        cycle_count: число циклов взлёт-посадка.

    Returns:
        R = налёт + τ_цикл × циклы, секунды.
    """
    return total_flight_time_s + assumptions.CYCLE_COST_S * cycle_count


def objective_value(mode: OptimizationMode, makespan_s: float, resource: float) -> float:
    """Значение критерия выбранного режима, секунды. Меньше — лучше.

    Args:
        mode: режим оптимизации.
        makespan_s: срок T, секунды.
        resource: ресурс R, секунды.

    Returns:
        T в режиме «минимум срока», R в режиме «минимум ресурса».
    """
    if mode is OptimizationMode.MIN_MAKESPAN:
        return makespan_s
    return resource


def weighted_value(alpha: float, makespan_s: float, resource: float) -> float:
    """Свёртка α·T + (1−α)·R — для фронта Парето (E12), секунды.

    Args:
        alpha: вес срока, доля 0..1.
        makespan_s: срок T, секунды.
        resource: ресурс R, секунды.

    Returns:
        Значение свёртки, секунды.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha должна лежать в [0, 1], получено {alpha}")
    return alpha * makespan_s + (1.0 - alpha) * resource


__all__ = ["objective_value", "resource_s", "weighted_value"]
