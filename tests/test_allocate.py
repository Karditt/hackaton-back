"""Распределение (E6): конструктивная эвристика против baseline (инвариант 8, E12)."""

from __future__ import annotations

import pytest

from core.allocate.baseline import ALGORITHM_NAME as BASELINE
from core.allocate.fleet import FleetInstance
from core.allocate.greedy import ALGORITHM_NAME as CONSTRUCTIVE
from core.allocate.search import ALGORITHM_NAME as LOCAL_SEARCH
from core.catalog import VehicleCatalog
from core.cli import load_scenario
from core.deconflict.placer import BookPlacer
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import MissionStatus, OptimizationMode, Scenario
from tests.conftest import SCENARIOS_DIR, small_scenario


@pytest.fixture(scope="module")
def demo_pair(
    runs: dict[str, PlanningRun], catalog: VehicleCatalog
) -> tuple[PlanningRun, PlanningRun]:
    """Демо: алгоритм по умолчанию (прогон сессии) и baseline на тех же подобластях."""
    default = runs["demo_100km2"]
    baseline = run_pipeline(default.scenario, catalog=catalog, allocator="baseline")
    return default, baseline


@pytest.fixture(scope="module")
def demo_greedy(
    runs: dict[str, PlanningRun], catalog: VehicleCatalog
) -> tuple[PlanningRun, list[tuple[tuple[str, int], tuple[str, ...], float]]]:
    """Демо: эвристика без поиска и старты, с которыми она ставила вылеты в расписание."""
    commits: list[tuple[tuple[str, int], tuple[str, ...], float]] = []
    original = BookPlacer.commit

    def spy(
        self: BookPlacer, instance: FleetInstance, site_id: str, cell_ids: list[str], start_s: float
    ) -> None:
        commits.append((instance.key, tuple(cell_ids), start_s))
        original(self, instance, site_id, cell_ids, start_s)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(BookPlacer, "commit", spy)
        run = run_pipeline(runs["demo_100km2"].scenario, catalog=catalog, allocator="constructive")
    return run, commits


def test_local_search_is_the_default_and_baseline_stays(
    demo_pair: tuple[PlanningRun, PlanningRun],
    demo_greedy: tuple[PlanningRun, list[tuple[tuple[str, int], tuple[str, ...], float]]],
) -> None:
    """По умолчанию — эвристика с поиском; эвристика без поиска и baseline запускаются
    и дают полный план (инвариант 8)."""
    default, baseline = demo_pair
    greedy, _ = demo_greedy
    assert default.mission.algorithm == LOCAL_SEARCH
    assert greedy.mission.algorithm == CONSTRUCTIVE
    assert baseline.mission.algorithm == BASELINE
    assert greedy.mission.status is MissionStatus.FEASIBLE
    assert baseline.mission.status is MissionStatus.FEASIBLE


def test_heuristic_and_schedule_agree_on_starts(
    demo_greedy: tuple[PlanningRun, list[tuple[tuple[str, int], tuple[str, ...], float]]],
) -> None:
    """Долг №54: эвристика видит задержки расписания — старты у них одни.

    Эвристика ставит каждый собранный вылет в книгу расписания; стадия
    расписания ставит итоговые вылеты в том же порядке и получает те же старты.
    Раньше эвристика считала, что борт взлетает, как только готов, а расписание
    потом сдвигало самолёты на час и больше.
    """
    run, commits = demo_greedy
    planned = {(key, cells): start for key, cells, start in commits}
    assert len(planned) == len(run.mission.sorties)
    for sortie in run.mission.sorties:
        key = ((sortie.vehicle_id, sortie.vehicle_instance), tuple(sortie.cell_ids))
        assert sortie.start_time_s == pytest.approx(planned[key])


def test_no_long_waits_for_separation_on_demo(
    demo_greedy: tuple[PlanningRun, list[tuple[tuple[str, int], tuple[str, ...], float]]],
) -> None:
    """Долг №54: у эвристики на демо ни один вылет не ждёт разведения дольше 15 минут.

    До 2026-09-27 (эвристика без книги расписания) два самолёта ждали первого
    старта 63 и 75 минут, коптер — 66 минут: временная зона у южной площадки и
    соседи на той же высоте съёмки. Локальный поиск ожидание допускает, если
    срок от этого короче, — поэтому проверяется эвристика без поиска.
    """
    schedule = demo_greedy[0].mission.schedule
    assert schedule is not None
    assert max(entry.delay_s for entry in schedule.entries) <= 15 * 60


def test_local_search_improves_on_the_heuristic(
    demo_pair: tuple[PlanningRun, PlanningRun],
    demo_greedy: tuple[PlanningRun, list[tuple[tuple[str, int], tuple[str, ...], float]]],
) -> None:
    """Шаг 3 E6: поиск разгружает хвост — демо заметно быстрее эвристики без поиска.

    На 2026-09-27: 4,12 ч → 3,81 ч. Порог с запасом: тест ловит поиск, который
    перестал принимать ходы, а не колебания в процентах.
    """
    default, _ = demo_pair
    greedy, _ = demo_greedy
    assert default.mission.metrics and greedy.mission.metrics
    assert default.mission.metrics.makespan_s < 0.97 * greedy.mission.metrics.makespan_s


def test_local_search_is_deterministic(catalog: VehicleCatalog) -> None:
    """AGENT.md §8: один сценарий — побитово один план."""
    scenario = load_scenario(SCENARIOS_DIR / "medium.json")
    first = run_pipeline(scenario, catalog=catalog)
    second = run_pipeline(scenario, catalog=catalog)
    assert first.result.allocation and second.result.allocation
    assert first.result.allocation.assignments == second.result.allocation.assignments
    assert first.mission.metrics == second.mission.metrics


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

    Без срока всё снимает один борт одним вылетом. Со сроком на секунду короче
    вылет режется по времени до срока, и остаток берёт второй борт. Сдвиг
    старта при разведении на общей площадке эвристика видит сама (долг №54):
    прежде тест держал минутный запас.
    """
    loose = small_scenario(
        mode="min_resource",
        fleet=[{"vehicle_id": "geoscan-gemini", "count": 2, "payload_id": "geoscan-pf1b"}],
    )
    free = run_pipeline(loose, catalog=catalog).mission
    assert free.metrics is not None and free.metrics.vehicle_count == 1
    deadline = free.metrics.makespan_s - 1.0
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
