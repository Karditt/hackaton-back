"""Сквозной прогон конвейера на всех тестовых сценариях."""

from __future__ import annotations

import pytest

from core.allocate.objective import objective_value
from core.catalog import VehicleCatalog
from core.cli import load_scenario
from core.flight.cost import endurance_limit_s, estimate_sortie_duration_s
from core.pipeline import STUB_STAGES, run_pipeline
from core.schemas import FlightPhase, Scenario, Severity
from tests.conftest import SCENARIOS_DIR


def test_pipeline_runs_on_every_scenario(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """AGENT.md §6: конвейер обязан отрабатывать на всех сценариях, включая «злой»."""
    run = run_pipeline(scenario, catalog=catalog)
    assert run.mission.sorties, "план не должен быть пустым"
    assert run.result.coverage.strips, "покрытие не должно быть пустым"
    assert run.result.timings_s


def test_pipeline_is_deterministic(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """AGENT.md §8: один сценарий и один seed дают побитово один план.

    Сравниваются вылеты, а не весь объект: `Mission.id` и `created_at`
    намеренно уникальны у каждого прогона.
    """
    first = run_pipeline(scenario, catalog=catalog).mission
    second = run_pipeline(scenario, catalog=catalog).mission
    assert first.metrics == second.metrics
    assert [sortie.model_dump() for sortie in first.sorties] == [
        sortie.model_dump() for sortie in second.sorties
    ]


def test_stub_stages_are_declared(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Заглушка обязана объявлять себя: план на заглушках не выглядит настоящим."""
    mission = run_pipeline(scenario, catalog=catalog).mission
    assert mission.is_stub()
    assert set(mission.stub_stages) == set(STUB_STAGES)


def test_every_sortie_starts_and_ends_on_the_ground(
    scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Инвариант 7: вылет — это фазы, а не одна линия «длина / скорость»."""
    mission = run_pipeline(scenario, catalog=catalog).mission
    for sortie in mission.sorties:
        phases = [leg.phase for leg in sortie.legs]
        assert phases[0] is FlightPhase.TAKEOFF
        assert phases[-1] is FlightPhase.LANDING
        assert FlightPhase.CLIMB in phases
        assert FlightPhase.DESCENT in phases
        assert FlightPhase.STRIP in phases


def test_sortie_duration_equals_sum_of_legs(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Длительность вылета — сумма фаз, а не отдельно посчитанное число."""
    mission = run_pipeline(scenario, catalog=catalog).mission
    for sortie in mission.sorties:
        assert sortie.duration_s == pytest.approx(
            sum(leg.duration_s for leg in sortie.legs), rel=1e-9
        )


def test_flight_time_is_not_distance_over_speed(
    simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Наивная оценка обязана расходиться с реальной: есть взлёт, набор, посадка."""
    mission = run_pipeline(simple_scenario, catalog=catalog).mission
    sortie = mission.sorties[0]
    vehicle = catalog.get(sortie.vehicle_id)
    naive_s = sortie.distance_m / vehicle.cruise_speed_mps
    assert sortie.duration_s > naive_s * 1.05


def test_allocated_sorties_fit_endurance(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Распределение не собирает вылеты, которые борт физически не вытянет."""
    run = run_pipeline(scenario, catalog=catalog)
    for sortie in run.mission.sorties:
        vehicle = catalog.get(sortie.vehicle_id)
        assert sortie.duration_s <= endurance_limit_s(vehicle) * 1.01


def test_estimate_matches_built_sortie(simple_scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Быстрая оценка распределения и полная сборка не должны расходиться.

    Расхождение означало бы, что распределение собирает вылеты, которые
    валидатор тут же бракует.
    """
    run = run_pipeline(simple_scenario, catalog=catalog)
    sortie = run.mission.sorties[0]
    vehicle = catalog.get(sortie.vehicle_id)
    strips = [run.result.coverage.strip(strip_id) for strip_id in sortie.strip_ids]
    estimate_s = estimate_sortie_duration_s(
        vehicle,
        run.result.survey_plan.for_vehicle(vehicle.id),
        run.work_area.site(sortie.site_id),
        [strip for strip in strips if strip is not None],
        simple_scenario.wind,
    )
    assert estimate_s == pytest.approx(sortie.duration_s, rel=0.02)


def test_metrics_match_sorties(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Метрики считаются по плану, а не живут отдельной жизнью."""
    mission = run_pipeline(scenario, catalog=catalog).mission
    metrics = mission.metrics
    assert metrics.sortie_count == len(mission.sorties)
    assert metrics.total_flight_time_s == pytest.approx(
        sum(sortie.duration_s for sortie in mission.sorties)
    )
    assert metrics.makespan_s == pytest.approx(
        max(sortie.end_time_s() for sortie in mission.sorties)
    )
    assert metrics.objective_value == pytest.approx(
        objective_value(metrics.makespan_s, metrics.total_flight_time_s, metrics.alpha)
    )


def test_makespan_never_exceeds_total_flight_time(
    scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Борта летают параллельно: makespan не может превышать суммарный налёт.

    Исключение — единственный борт: тогда величины сходятся с точностью до
    времени подготовки и замены АКБ.
    """
    metrics = run_pipeline(scenario, catalog=catalog).mission.metrics
    if metrics.vehicle_count > 1:
        assert metrics.makespan_s <= metrics.total_flight_time_s


def test_alpha_is_carried_through(simple_scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Переключение критерия доходит до плана и меняет значение целевой функции."""
    makespan_first = run_pipeline(simple_scenario, catalog=catalog, alpha=1.0).mission
    flight_time_first = run_pipeline(simple_scenario, catalog=catalog, alpha=0.0).mission
    assert makespan_first.alpha == 1.0
    assert flight_time_first.alpha == 0.0
    assert makespan_first.metrics.objective_value == pytest.approx(
        makespan_first.metrics.makespan_s
    )
    assert flight_time_first.metrics.objective_value == pytest.approx(
        flight_time_first.metrics.total_flight_time_s
    )


def test_incompatible_vehicle_is_excluded_with_a_reason(catalog: VehicleCatalog) -> None:
    """Геоскан 201 без размеров матрицы RGB-камер отсеивается, а не считается.

    Это проверка инварианта 6 на уровне конвейера: вместо правдоподобного
    числа выдаётся внятное предупреждение.
    """
    run = run_pipeline(load_scenario(SCENARIOS_DIR / "medium.json"), catalog=catalog)
    excluded = [
        warning for warning in run.result.survey_plan.warnings if warning.severity is Severity.ERROR
    ]
    assert excluded, "борт без пригодной нагрузки обязан дать предупреждение"
    assert all(sortie.vehicle_id != "geoscan-201" for sortie in run.mission.sorties), (
        "исключённый борт не должен получать вылеты"
    )


def test_nasty_scenario_reports_its_problems(catalog: VehicleCatalog) -> None:
    """«Злой» сценарий обязан жаловаться, а не молча выдавать красивый план."""
    run = run_pipeline(load_scenario(SCENARIOS_DIR / "nasty.json"), catalog=catalog)
    errors = [warning for warning in run.result.warnings() if warning.severity is Severity.ERROR]
    assert errors, "невыполнимость обязана быть видна"
