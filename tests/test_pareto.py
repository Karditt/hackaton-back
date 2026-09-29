"""Фронт Парето «срок — ресурс» (E12, заказчик №23, №52)."""

from __future__ import annotations

from core.catalog import VehicleCatalog
from core.pareto import pareto_front
from core.schemas import OptimizationMode, Scenario
from tests.conftest import small_scenario


def test_front_spans_both_modes_and_is_consistent(
    simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Края — планы двух режимов, промежуточные — ресурс к сроку между ними.

    Промежуточный план не дороже плана «минимум срока»: тот успевает к любому
    сроку позже своего, и конвейер его пробует (`_allocate`).
    """
    seen = []
    points = pareto_front(simple_scenario, catalog, steps=1, on_point=seen.append)
    assert len(points) == len(seen) == 3
    fastest, middle, frugal = points
    assert fastest.mode is OptimizationMode.MIN_MAKESPAN and fastest.deadline_s is None
    assert frugal.mode is OptimizationMode.MIN_RESOURCE and frugal.deadline_s is None
    assert all(point.feasible for point in points)
    assert fastest.makespan_s <= middle.makespan_s <= middle.deadline_s
    assert middle.deadline_s < frugal.makespan_s
    assert frugal.resource_s <= middle.resource_s <= fastest.resource_s
    assert not any(point.dominated for point in points)


def test_infeasible_scenario_has_only_the_edges(catalog: VehicleCatalog) -> None:
    """Задание невыполнимо даже без срока — промежуточных точек нет."""
    far = small_scenario(sites=[{"id": "base", "position": [30.31, 59.80]}])
    points = pareto_front(far, catalog, steps=3)
    assert [point.feasible for point in points] == [False, False]
