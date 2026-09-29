"""Распределение (E6): конструктивная эвристика против baseline (инвариант 8, E12)."""

from __future__ import annotations

import pytest

from core.allocate.baseline import ALGORITHM_NAME as BASELINE
from core.allocate.fleet import FleetInstance
from core.allocate.greedy import ALGORITHM_NAME as CONSTRUCTIVE
from core.allocate.objective import service_share
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
    """Демо: эвристика без поиска и старты, с которыми она ставила вылеты в расписание.

    Конвейер пробует несколько стратегий эвристики и целевых сроков, у каждого
    прогона своя книга расписания; возвращаются постановки той, чей план взят.
    Книги различаются самими объектами, а не `id()`: освобождённую книгу
    сборщик мусора отдаёт, и её `id` достаётся следующей. Расчёт — в этом
    процессе (`workers=1`): в процессах пула подмены `commit` нет.
    """
    commits: dict[BookPlacer, list[tuple[tuple[str, int], tuple[str, ...], float]]] = {}
    original = BookPlacer.commit

    def spy(
        self: BookPlacer, instance: FleetInstance, site_id: str, cell_ids: list[str], start_s: float
    ) -> None:
        commits.setdefault(self, []).append((instance.key, tuple(cell_ids), start_s))
        original(self, instance, site_id, cell_ids, start_s)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(BookPlacer, "commit", spy)
        run = run_pipeline(
            runs["demo_100km2"].scenario, catalog=catalog, allocator="constructive", workers=1
        )
    flown = {
        ((sortie.vehicle_id, sortie.vehicle_instance), tuple(sortie.cell_ids))
        for sortie in run.mission.sorties
    }
    chosen = next(
        group
        for group in commits.values()
        if {(key, cells) for key, cells, _start in group} == flown
    )
    return run, chosen


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
    runs: dict[str, PlanningRun], catalog: VehicleCatalog
) -> None:
    """Долг №54: у эвристики на демо ни один вылет не ждёт разведения дольше получаса.

    До 2026-09-27 (эвристика без книги расписания) два самолёта ждали первого
    старта 63 и 75 минут, коптер — 66 минут: временная зона у южной площадки и
    соседи на той же высоте съёмки. Локальный поиск ожидание допускает, если
    срок от этого короче, — поэтому проверяется эвристика без поиска.

    С 2026-09-28 коптер начинает вылет с ближней работы (долг №59), а ближняя
    у южной площадки лежит во временной зоне ОРВД: переждать её закрытие —
    выгоднее лететь далеко ради минут съёмки, и эвристика это видит и выбирает
    сама. Поэтому разведение проверяется на демо без временных зон: ждать
    соседа по небу долго нельзя, окна ОРВД — можно.

    Порог — полчаса, а не 15 минут: самолёты с 2026-09-28 начинают со своей
    зоны площадки, и первый вылет южного самолёта сходится зонами манёвра с
    восточным на стыке их зон — эвристика видит сдвиг, пробует другие вылеты и
    берёт ожидание 25 минут, потому что за занятое время оно снимает больше.
    Срок эвристики при этом 2,95 ч; стратегия «секторы бортов» — 3,42 ч и
    ожидание 31 минута. Тест ловит возврат к ожиданиям на час и больше.
    """
    scenario = runs["demo_100km2"].scenario
    permanent = [zone for zone in scenario.no_fly_zones if zone.is_permanent()]
    assert len(permanent) < len(scenario.no_fly_zones), "в демо должна быть временная зона"
    calm = scenario.model_copy(update={"no_fly_zones": permanent})
    run = run_pipeline(calm, catalog=catalog, allocator="constructive")
    schedule = run.mission.schedule
    assert schedule is not None
    assert max(entry.delay_s for entry in schedule.entries) <= 30 * 60


def test_local_search_improves_on_the_heuristic(
    demo_pair: tuple[PlanningRun, PlanningRun],
    demo_greedy: tuple[PlanningRun, list[tuple[tuple[str, int], tuple[str, ...], float]]],
) -> None:
    """Шаг 3 E6: поиск принимает ходы — демо не медленнее эвристики и заметно лучше по критерию.

    На 2026-09-27: 4,12 ч → 3,81 ч. С 2026-09-28 эвристика сама разводит борта
    по зонам и переставляет подобласти вылета, и на демо ей остаётся мало
    хвоста: срок 3,43 → 3,42 ч, зато поиск растворяет короткие вылеты — налёт
    −15%. С 2026-09-29 эвристика ещё и прогоняется под целевые сроки, и поиску
    на демо остаётся совсем мало: срок тот же, 2,84 ч, ресурс −2,5%. Критерий
    режима «минимум срока» — срок, при равенстве — ресурс
    (`core.allocate.search`); порог ловит поиск, который перестал принимать
    ходы, а не колебания в процентах.
    """
    default, _ = demo_pair
    greedy, _ = demo_greedy
    fast, slow = default.mission.metrics, greedy.mission.metrics
    assert fast and slow
    assert fast.makespan_s <= slow.makespan_s + 1.0
    assert fast.makespan_s < slow.makespan_s - 60.0 or fast.resource_s < 0.99 * slow.resource_s


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


def test_service_wear_follows_customer_norms(runs: dict[str, PlanningRun]) -> None:
    """Долг №44: износ по нормативам ТО (№50) — в плане информацией, без выдуманных чисел."""
    assert service_share("geoscan-201", 3, 7200.0) == pytest.approx(3 / 80)
    assert service_share("geoscan-801", 1, 8 * 3600.0) == pytest.approx(8 / 160)
    assert service_share("geoscan-gemini", 5, 3600.0) is None
    notes = [w for w in runs["medium"].mission.warnings if w.code == "plan.service_wear"]
    assert len(notes) == 1
    assert "для Геоскан Gemini норматив не задан" in notes[0].message


def test_unknown_allocator_is_refused(simple_scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Неизвестный алгоритм распределения — ошибка вызова, а не тихий baseline."""
    with pytest.raises(ValueError, match="неизвестен"):
        run_pipeline(simple_scenario, catalog=catalog, allocator="magic")
