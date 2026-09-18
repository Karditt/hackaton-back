"""Краш-тест (заказчик, №4): намеренно испорченный план обязан быть отвергнут.

Валидатор проверяет готовые вылеты заново и не доверяет стадиям, которые их
строили. Здесь берутся выполнимые планы, в них вносится ровно одно нарушение
жёсткого правила, и проверяется, что валидатор его находит (инвариант 9).
"""

from __future__ import annotations

import pytest

from core.pipeline import PlanningRun
from core.schemas import FlightPhase, Sortie
from core.validate import validate_plan
from tests.conftest import FEASIBLE


def _check(run: PlanningRun, sorties: list[Sortie]) -> set[str]:
    assert run.result.decomposition is not None
    report = validate_plan(
        run.scenario,
        run.work_area,
        run.result.decomposition,
        run.result.coverage,
        sorties,
        run.vehicles,
    )
    return {violation.code for violation in report.violations}


def _with_waypoint_at(sortie: Sortie, phase: FlightPhase, x: float, y: float) -> Sortie:
    """Копия вылета, в первый участок фазы которого вставлена точка (x, y)."""
    legs = list(sortie.legs)
    index = next(i for i, leg in enumerate(legs) if leg.phase is phase)
    waypoints = list(legs[index].waypoints)
    waypoints.insert(1, waypoints[0].model_copy(update={"x_m": x, "y_m": y}))
    legs[index] = legs[index].model_copy(update={"waypoints": waypoints})
    return sortie.model_copy(update={"legs": legs})


@pytest.mark.parametrize("name", sorted(FEASIBLE))
def test_feasible_plans_pass_the_crash_test(runs: dict[str, PlanningRun], name: str) -> None:
    """Выданный план сам по себе проходит все жёсткие проверки."""
    run = runs[name]
    assert _check(run, run.mission.sorties) == set()


def test_flight_through_no_fly_zone_is_caught(runs: dict[str, PlanningRun]) -> None:
    """Перелёт через постоянную зону без высотных ограничений — нарушение."""
    run = runs["medium"]
    zone = next(zone for zone in run.work_area.zones if zone.permanent)
    center = zone.footprint.centroid
    sorties = list(run.mission.sorties)
    sorties[0] = _with_waypoint_at(sorties[0], FlightPhase.TRANSIT, center.x, center.y)
    assert "validate.no_fly_zone" in _check(run, sorties)


def test_close_approach_is_caught(runs: dict[str, PlanningRun]) -> None:
    """Второй борт по той же траектории в то же время — опасное сближение."""
    run = runs["simple"]
    original = run.mission.sorties[0]
    twin = original.model_copy(update={"id": f"{original.id}-twin", "vehicle_instance": 99})
    assert "validate.separation" in _check(run, [*run.mission.sorties, twin])


def test_battery_overuse_is_caught(runs: dict[str, PlanningRun]) -> None:
    """Расход сверх 80% — нарушение отсечки заряда (№34)."""
    run = runs["simple"]
    sorties = list(run.mission.sorties)
    sorties[0] = sorties[0].model_copy(update={"energy_fraction_used": 0.95})
    assert "validate.energy" in _check(run, sorties)


def test_leaving_the_altitude_corridor_is_caught(runs: dict[str, PlanningRun]) -> None:
    """Точка выше коридора — нарушение (№10)."""
    run = runs["simple"]
    sorties = list(run.mission.sorties)
    legs = list(sorties[0].legs)
    waypoints = list(legs[1].waypoints)
    waypoints[-1] = waypoints[-1].model_copy(update={"alt_m": 1000.0})
    legs[1] = legs[1].model_copy(update={"waypoints": waypoints})
    sorties[0] = sorties[0].model_copy(update={"legs": legs})
    assert "validate.corridor" in _check(run, sorties)


def test_missing_cells_are_caught(runs: dict[str, PlanningRun]) -> None:
    """Выброшенный вылет оставляет подобласти неснятыми — задание не выполнено (инв. 10)."""
    run = runs["simple"]
    assert "validate.coverage_incomplete" in _check(run, run.mission.sorties[:-1])


def test_takeoff_from_wrong_site_is_caught(runs: dict[str, PlanningRun]) -> None:
    """Борт не может взлететь не там, где сел."""
    run = runs["demo_100km2"]
    sorties = list(run.mission.sorties)
    trips: dict[tuple[str, int], list[int]] = {}
    for index, sortie in enumerate(sorties):
        trips.setdefault((sortie.vehicle_id, sortie.vehicle_instance), []).append(index)
    later = next(indexes[1] for indexes in trips.values() if len(indexes) > 1)
    other_site = next(
        site.id
        for site in run.scenario.primary_sites()
        if site.id != sorties[later].takeoff_site_id
    )
    sorties[later] = sorties[later].model_copy(update={"takeoff_site_id": other_site})
    assert "validate.site_continuity" in _check(run, sorties)


def test_pass_next_to_tall_obstacle_is_caught(runs: dict[str, PlanningRun]) -> None:
    """Съёмка ниже вершины мачты с запасом рядом с ней — опасный пролёт (№4)."""
    run = runs["demo_100km2"]
    mast = next(item for item in run.work_area.obstacles if item.id == "mast-north")
    sorties = list(run.mission.sorties)
    sorties[0] = _with_waypoint_at(
        sorties[0], FlightPhase.STRIP, mast.footprint.centroid.x, mast.footprint.centroid.y
    )
    assert "validate.obstacle" in _check(run, sorties)


def test_unholdable_ground_speed_is_caught(runs: dict[str, PlanningRun]) -> None:
    """Путевая скорость на галсе, которую борт не удержит против ветра, — нарушение (№45)."""
    run = runs["simple"]
    sorties = list(run.mission.sorties)
    legs = [
        leg.model_copy(update={"ground_speed_mps": 30.0}) if leg.phase is FlightPhase.STRIP else leg
        for leg in sorties[0].legs
    ]
    sorties[0] = sorties[0].model_copy(update={"legs": legs})
    assert "validate.airspeed" in _check(run, sorties)
