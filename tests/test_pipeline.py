"""Сквозной прогон конвейера: полный план или отчёт о невыполнимости."""

from __future__ import annotations

import pytest

from core import assumptions
from core.allocate.objective import objective_value, resource_s, weighted_value
from core.catalog import VehicleCatalog
from core.flight.cost import endurance_limit_s
from core.pipeline import STUB_STAGES, PlanningRun, run_pipeline
from core.schemas import (
    FlightPhase,
    MissionStatus,
    OptimizationMode,
    RecommendationKind,
    Scenario,
)
from tests.conftest import FEASIBLE, INFEASIBLE, small_scenario


def _feasible(run: PlanningRun) -> bool:
    return run.mission.status is MissionStatus.FEASIBLE


def test_every_scenario_ends_as_intended(run: PlanningRun, scenario_name: str) -> None:
    """AGENT.md §6: конвейер отрабатывает на всех сценариях и даёт задуманный итог."""
    if scenario_name in FEASIBLE:
        assert _feasible(run), [reason.message for reason in run.mission.reasons]
    if scenario_name in INFEASIBLE:
        assert not _feasible(run)
    assert run.result.timings_s


def test_no_partial_plans(run: PlanningRun) -> None:
    """Инвариант 10: либо полный план, либо отчёт без маршрутов с причиной и советом."""
    mission = run.mission
    if _feasible(run):
        assert mission.sorties and mission.schedule and mission.metrics
        assert not mission.reasons
        assert mission.metrics.cells_covered == mission.metrics.cells_planned
    else:
        assert not mission.sorties
        assert mission.schedule is None and mission.metrics is None
        assert mission.reasons
        assert mission.recommendations


def test_pipeline_is_deterministic(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """AGENT.md §8: один сценарий и один seed дают побитово один план."""
    first = run_pipeline(scenario, catalog=catalog).mission
    second = run_pipeline(scenario, catalog=catalog).mission
    assert first.metrics == second.metrics
    assert [s.model_dump() for s in first.sorties] == [s.model_dump() for s in second.sorties]


def test_stub_stages_are_declared(run: PlanningRun) -> None:
    """Заглушка обязана объявлять себя: план на заглушках не выглядит настоящим.

    Советы невыполнимого плана с 2026-09-29 проверяются расчётом и заглушкой
    не считаются; как они построены — справка `advise.rules`.
    """
    assert set(run.mission.stub_stages) == set(STUB_STAGES)
    if not _feasible(run):
        assert any(item.code == "advise.rules" for item in run.mission.warnings)


def test_every_sortie_is_a_sequence_of_phases(run: PlanningRun) -> None:
    """Инвариант 7: вылет — взлёт, набор, перелёт, съёмка, снижение, посадка."""
    for sortie in run.mission.sorties:
        phases = [leg.phase for leg in sortie.legs]
        assert phases[0] is FlightPhase.TAKEOFF
        assert phases[-1] is FlightPhase.LANDING
        assert {FlightPhase.CLIMB, FlightPhase.DESCENT, FlightPhase.STRIP} <= set(phases)
        assert sortie.duration_s == pytest.approx(sum(leg.duration_s for leg in sortie.legs))


def test_strip_time_is_length_over_held_ground_speed(run: PlanningRun) -> None:
    """№45: на галсе автопилот держит путевую скорость — время галса = длина / скорость."""
    for sortie in run.mission.sorties:
        for leg in sortie.legs:
            if leg.phase is FlightPhase.STRIP:
                assert leg.duration_s == pytest.approx(leg.length_m / leg.ground_speed_mps)


def test_sortie_is_longer_than_distance_over_speed(runs: dict[str, PlanningRun]) -> None:
    """Инвариант 7: фазы (взлёт, набор, посадка) делают вылет дольше «длина / скорость»."""
    run = runs["simple"]
    sortie = run.mission.sorties[0]
    params = run.result.survey_plan.for_vehicle(sortie.vehicle_id)
    assert params is not None
    assert sortie.duration_s > sortie.distance_m / params.transit_speed_mps * 1.05


def test_wind_does_not_enter_flight_time(catalog: VehicleCatalog) -> None:
    """№36, №45: ветер не влияет на время, если он не заставил снизить путевую скорость."""
    calm = run_pipeline(small_scenario(wind={"speed_mps": 0, "direction_deg": 0}), catalog=catalog)
    breezy = run_pipeline(
        small_scenario(wind={"speed_mps": 4, "direction_deg": 90}), catalog=catalog
    )
    assert calm.mission.metrics is not None and breezy.mission.metrics is not None
    assert breezy.mission.metrics.total_flight_time_s == pytest.approx(
        calm.mission.metrics.total_flight_time_s
    )


def test_energy_is_linear_in_time_with_20_percent_cutoff(
    run: PlanningRun, catalog: VehicleCatalog
) -> None:
    """№34, №35: расход — доля автономности, не больше 80%."""
    for sortie in run.mission.sorties:
        vehicle = catalog.get(sortie.vehicle_id)
        assert sortie.energy_fraction_used == pytest.approx(sortie.duration_s / vehicle.endurance_s)
        assert sortie.duration_s <= endurance_limit_s(vehicle) + 1e-6


def test_one_survey_altitude_and_transit_inside_corridor(
    run: PlanningRun, scenario: Scenario
) -> None:
    """№10: съёмка на одной высоте, всё — внутри коридора, перелёт выше съёмки."""
    corridor = scenario.altitude_corridor.max_altitude_agl_m
    for sortie in run.mission.sorties:
        strip_alts = {
            round(wp.alt_m, 3)
            for leg in sortie.legs
            if leg.phase is FlightPhase.STRIP
            for wp in leg.waypoints
        }
        assert strip_alts == {round(sortie.survey_altitude_m, 3)}
        assert sortie.transit_altitude_m <= corridor
        assert sortie.transit_altitude_m > sortie.survey_altitude_m


def test_metrics_follow_the_criteria(run: PlanningRun) -> None:
    """PROJECT_BRIEF.md §4.3: срок, ресурс с циклами, критерий режима, свёртка."""
    metrics = run.mission.metrics
    if metrics is None:
        return
    sorties = run.mission.sorties
    assert metrics.cycle_count == len(sorties)
    assert metrics.makespan_s == pytest.approx(max(s.end_time_s() for s in sorties))
    assert metrics.resource_s == pytest.approx(
        resource_s(sum(s.duration_s for s in sorties), len(sorties))
    )
    assert metrics.objective_value == pytest.approx(
        objective_value(metrics.mode, metrics.makespan_s, metrics.resource_s)
    )
    assert metrics.weighted_value == pytest.approx(
        weighted_value(metrics.alpha, metrics.makespan_s, metrics.resource_s)
    )


def test_mode_switch_changes_the_criterion(
    simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """№23: минимум срока — критерий T, минимум ресурса — R."""
    fast = run_pipeline(simple_scenario, catalog=catalog, mode=OptimizationMode.MIN_MAKESPAN)
    frugal = run_pipeline(simple_scenario, catalog=catalog, mode=OptimizationMode.MIN_RESOURCE)
    assert fast.mission.metrics and frugal.mission.metrics
    assert fast.mission.metrics.objective_value == pytest.approx(fast.mission.metrics.makespan_s)
    assert frugal.mission.metrics.objective_value == pytest.approx(
        frugal.mission.metrics.resource_s
    )


def test_vehicle_takes_off_where_it_landed(run: PlanningRun) -> None:
    """Следующий вылет борта — с той площадки, где закончился предыдущий, и после посадки."""
    trips: dict[tuple[str, int], list] = {}
    for sortie in run.mission.sorties:
        trips.setdefault((sortie.vehicle_id, sortie.vehicle_instance), []).append(sortie)
    for items in trips.values():
        items.sort(key=lambda s: s.start_time_s)
        for previous, current in zip(items, items[1:], strict=False):
            assert current.takeoff_site_id == previous.landing_site_id
            assert current.start_time_s >= previous.end_time_s()


def test_demo_uses_fixed_wing_and_excludes_inspection_drone(runs: dict[str, PlanningRun]) -> None:
    """Демо: самолёт снимает RGB (кадр Riebo выведен из Мп и формата), 801 исключён."""
    run = runs["demo_100km2"]
    vehicles = {sortie.vehicle_id for sortie in run.mission.sorties}
    assert "geoscan-201" in vehicles
    assert "geoscan-801" not in vehicles
    assert any(
        w.context.get("vehicle_id") == "geoscan-801" for w in run.result.survey_plan.warnings
    )


def test_nasty_is_infeasible_for_lack_of_sorties(runs: dict[str, PlanningRun]) -> None:
    """«Злой» сценарий: предел вылетов — причина названа, совет дан (№42)."""
    mission = runs["nasty"].mission
    assert any(reason.code == "allocate.no_trips_left" for reason in mission.reasons)
    assert any(item.kind is RecommendationKind.ADD_BATTERIES for item in mission.recommendations)


def test_far_area_gets_add_site_advice_with_a_place(catalog: VehicleCatalog) -> None:
    """№33: «самая удалённая точка слишком далеко» — и где поставить площадку.

    Совет проверен расчётом (E15): с площадкой в предложенном месте задание
    выполнимо — распределение само ставит на неё не приписанный борт.
    """
    far = small_scenario(sites=[{"id": "base", "position": [30.31, 59.80]}])
    mission = run_pipeline(far, catalog=catalog).mission
    assert mission.status is MissionStatus.INFEASIBLE
    advice = [item for item in mission.recommendations if item.kind is RecommendationKind.ADD_SITE]
    assert advice and advice[0].position is not None
    assert advice[0].checked is True
    assert "Проверено расчётом" in advice[0].message


def test_advice_is_checked_by_planning(runs: dict[str, PlanningRun]) -> None:
    """E15: совет «снимите предел вылетов» у «злого» сценария проверен расчётом."""
    mission = runs["nasty"].mission
    batteries = next(
        item for item in mission.recommendations if item.kind is RecommendationKind.ADD_BATTERIES
    )
    assert batteries.checked is not None
    assert "Проверено расчётом: без предела числа вылетов" in batteries.message
    unchecked = [
        item
        for item in mission.recommendations
        if item.kind in (RecommendationKind.OTHER_MODEL, RecommendationKind.WAIT)
    ]
    assert all(item.checked is None for item in unchecked)


def test_hints_and_fronts_do_not_check_advice(catalog: VehicleCatalog) -> None:
    """Проверка советов — ещё прогоны; там, где прогонов много, её нет."""
    far = small_scenario(sites=[{"id": "base", "position": [30.31, 59.80]}])
    mission = run_pipeline(far, catalog=catalog, verify=False).mission
    assert mission.recommendations
    assert all(item.checked is None for item in mission.recommendations)


def test_no_suitable_vehicle_is_infeasible_not_an_exception(catalog: VehicleCatalog) -> None:
    """Флот без подходящей камеры — отчёт о невыполнимости, а не падение."""
    mission = run_pipeline(
        small_scenario(fleet=[{"vehicle_id": "geoscan-801"}]), catalog=catalog
    ).mission
    assert mission.status is MissionStatus.INFEASIBLE
    assert any(item.kind is RecommendationKind.OTHER_MODEL for item in mission.recommendations)


def test_missed_deadline_is_infeasible(catalog: VehicleCatalog) -> None:
    """№23: ограничение по сроку — жёсткое; не уложились — невыполнимо с советом."""
    mission = run_pipeline(small_scenario(deadline_s=600), catalog=catalog).mission
    assert mission.status is MissionStatus.INFEASIBLE
    assert any(reason.code == "validate.deadline" for reason in mission.reasons)
    assert any(item.kind is RecommendationKind.ADD_VEHICLE for item in mission.recommendations)


def test_fleet_is_a_pool_and_composition_is_a_result(runs: dict[str, PlanningRun]) -> None:
    """Долг №41 (№48): флот сценария — пул, состав плана — результат.

    В демо 10 бортов пула, в работе 9: у Геоскан 801 нет камеры для этой
    съёмки. Состав называет площадку каждого борта — штатную.
    """
    run = runs["demo_100km2"]
    mission = run.mission
    assert mission.metrics is not None
    pool = sum(unit.count for unit in run.scenario.fleet)
    used = sum(unit.count for unit in mission.fleet_used)
    idle = sum(unit.count for unit in mission.fleet_idle)
    assert used + idle == pool
    assert used == mission.metrics.vehicle_count
    assert "geoscan-801" in {unit.vehicle_id for unit in mission.fleet_idle}
    primary = {site.id for site in run.scenario.primary_sites()}
    assert all(unit.site_id in primary for unit in mission.fleet_used)
    note = next(item for item in mission.warnings if item.code == "plan.fleet_idle")
    assert f"В работе {used} из {pool}" in note.message


def test_plan_composition_works_as_a_fleet(
    simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Состав плана — годный флот: «использовать ровно это» даёт выполнимый план."""
    first = run_pipeline(simple_scenario, catalog=catalog, mode=OptimizationMode.MIN_RESOURCE)
    assert first.mission.fleet_used
    again = run_pipeline(
        simple_scenario.model_copy(update={"fleet": first.mission.fleet_used}),
        catalog=catalog,
        mode=OptimizationMode.MIN_RESOURCE,
    )
    assert again.mission.status is MissionStatus.FEASIBLE
    assert not again.mission.fleet_idle


def test_parallel_allocation_gives_the_same_plan(catalog: VehicleCatalog, runs) -> None:
    """Долг №62: варианты распределения в процессах пула — тот же план, что в одном процессе."""
    scenario = runs["medium"].scenario
    alone = run_pipeline(scenario, catalog=catalog, workers=1).mission
    pooled = run_pipeline(scenario, catalog=catalog, workers=2).mission
    assert alone.metrics == pooled.metrics
    assert [s.model_dump() for s in alone.sorties] == [s.model_dump() for s in pooled.sorties]


def test_cycle_cost_is_a_registered_assumption() -> None:
    """Стоимость цикла взлёт-посадка — допущение команды с обоснованием (№23)."""
    item = assumptions.get("CYCLE_COST_S")
    assert item.is_team_assumption()
    assert item.rationale
