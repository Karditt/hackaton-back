"""Распределение (E6): конструктивная эвристика против baseline (инвариант 8, E12)."""

from __future__ import annotations

import pytest

from core.allocate.baseline import ALGORITHM_NAME as BASELINE
from core.allocate.greedy import ALGORITHM_NAME as CONSTRUCTIVE
from core.catalog import VehicleCatalog
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import MissionStatus, OptimizationMode, Scenario
from tests.conftest import small_scenario


@pytest.fixture(scope="module")
def demo_pair(
    runs: dict[str, PlanningRun], catalog: VehicleCatalog
) -> tuple[PlanningRun, PlanningRun]:
    """Демо: эвристика (прогон сессии) и baseline на тех же подобластях."""
    constructive = runs["demo_100km2"]
    baseline = run_pipeline(constructive.scenario, catalog=catalog, allocator="baseline")
    return constructive, baseline


def test_constructive_is_the_default_and_baseline_stays(
    demo_pair: tuple[PlanningRun, PlanningRun],
) -> None:
    """По умолчанию — эвристика; baseline запускается и даёт полный план (инвариант 8)."""
    constructive, baseline = demo_pair
    assert constructive.mission.algorithm == CONSTRUCTIVE
    assert baseline.mission.algorithm == BASELINE
    assert baseline.mission.status is MissionStatus.FEASIBLE


def test_constructive_beats_baseline_on_demo(demo_pair: tuple[PlanningRun, PlanningRun]) -> None:
    """E12: на демо эвристика заметно быстрее baseline — время делится, а не площадь.

    На 2026-09-27: срок 13,9 ч → 4,2 ч. Порог с запасом: тест ловит возврат к
    делению по площади, а не колебания в процентах.
    """
    constructive, baseline = demo_pair
    assert constructive.mission.metrics and baseline.mission.metrics
    assert constructive.mission.metrics.makespan_s < 0.6 * baseline.mission.metrics.makespan_s
    assert constructive.mission.metrics.resource_s < baseline.mission.metrics.resource_s


def test_resource_mode_spends_less_resource(
    demo_pair: tuple[PlanningRun, PlanningRun], catalog: VehicleCatalog
) -> None:
    """№23: в режиме «минимум ресурса» ресурс меньше, срок — больше, чем в «минимуме срока»."""
    fast, _ = demo_pair
    frugal = run_pipeline(fast.scenario, catalog=catalog, mode=OptimizationMode.MIN_RESOURCE)
    assert fast.mission.metrics and frugal.mission.metrics
    assert frugal.mission.metrics.resource_s < fast.mission.metrics.resource_s
    assert frugal.mission.metrics.makespan_s >= fast.mission.metrics.makespan_s


def test_resource_mode_respects_the_deadline(catalog: VehicleCatalog) -> None:
    """Минимум ресурса — при сроке не больше дедлайна.

    Без срока всё снимает один борт одним вылетом. Со сроком чуть короче
    вылет режется по времени до срока, и остаток берёт второй борт. Запас в
    минуту — на сдвиг старта при разведении на общей площадке: распределение
    задержек расписания не видит (долг №54).
    """
    loose = small_scenario(
        mode="min_resource",
        fleet=[{"vehicle_id": "geoscan-gemini", "count": 2, "payload_id": "geoscan-pf1b"}],
    )
    free = run_pipeline(loose, catalog=catalog).mission
    assert free.metrics is not None and free.metrics.vehicle_count == 1
    deadline = free.metrics.makespan_s - 60.0
    bound = run_pipeline(loose.model_copy(update={"deadline_s": deadline}), catalog=catalog)
    metrics = bound.mission.metrics
    assert metrics is not None and metrics.makespan_s <= deadline
    assert metrics.vehicle_count == 2


def _long_area(**overrides) -> Scenario:
    """Полоса 12 км с площадками на концах: коптер с одной площадки дальний край не достанет."""
    payload = {
        "area": [[[30.300, 59.900], [30.520, 59.900], [30.520, 59.906], [30.300, 59.906]]],
        "sites": [
            {"id": "west", "position": [30.302, 59.898]},
            {"id": "east", "position": [30.518, 59.898]},
        ],
        "fleet": [{"vehicle_id": "geoscan-gemini", "count": 2, "payload_id": "geoscan-pf1b"}],
    }
    payload.update(overrides)
    return small_scenario(**payload)


def test_unassigned_boards_are_placed_where_their_work_is(catalog: VehicleCatalog) -> None:
    """Долг №53: борт без площадки ставится туда, где есть досягаемая работа.

    Коптеры не приписаны к площадкам; каждая половина полосы достижима только
    со своего конца. Эвристика ставит по коптеру на каждую площадку.
    """
    run = run_pipeline(_long_area(), catalog=catalog)
    assert run.mission.status is MissionStatus.FEASIBLE, [r.message for r in run.mission.reasons]
    assert {sortie.takeoff_site_id for sortie in run.mission.sorties} == {"west", "east"}


def test_unknown_allocator_is_refused(simple_scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Неизвестный алгоритм распределения — ошибка вызова, а не тихий baseline."""
    with pytest.raises(ValueError, match="неизвестен"):
        run_pipeline(simple_scenario, catalog=catalog, allocator="magic")
