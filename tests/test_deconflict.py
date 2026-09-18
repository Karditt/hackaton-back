"""Разведение бортов (E14) и обход зон (E5): эшелоны, расписание, граф видимости."""

from __future__ import annotations

from shapely.geometry import LineString

from core.geo.mask import build_work_area
from core.pipeline import PlanningRun
from core.schemas import FlightPhase, MetricPoint
from core.transit.routing import make_router
from tests.conftest import small_scenario

_WALL = [[30.305, 59.880], [30.315, 59.880], [30.315, 59.920], [30.305, 59.920]]


def test_each_vehicle_gets_its_own_transit_level(runs: dict[str, PlanningRun]) -> None:
    """№10: пока эшелонов в коридоре хватает, у каждого борта — свой."""
    run = runs["demo_100km2"]
    levels: dict[tuple[str, int], float] = {}
    for sortie in run.mission.sorties:
        levels[(sortie.vehicle_id, sortie.vehicle_instance)] = sortie.transit_altitude_m
    assert len(set(levels.values())) == len(levels)


def test_schedule_lists_every_sortie(run: PlanningRun) -> None:
    """№31: расписание — обязательный выход, в нём каждый вылет."""
    mission = run.mission
    if mission.schedule is None:
        return
    assert {entry.sortie_id for entry in mission.schedule.entries} == {
        s.id for s in mission.sorties
    }
    for entry in mission.schedule.entries:
        assert entry.end_s > entry.start_s >= 0


def test_temporary_zone_is_respected_in_time(runs: dict[str, PlanningRun]) -> None:
    """№40: над временной зоной летают только вне её окна."""
    run = runs["demo_100km2"]
    zone = next(zone for zone in run.work_area.zones if not zone.permanent)
    touched = 0
    for sortie in run.mission.sorties:
        for leg in sortie.legs:
            if leg.phase in (FlightPhase.TAKEOFF, FlightPhase.LANDING):
                continue
            points = [(wp.x_m, wp.y_m) for wp in leg.waypoints]
            if len(set(points)) < 2 or not LineString(points).intersects(zone.inner):
                continue
            touched += 1
            assert not zone.active_during(leg.waypoints[0].time_s, leg.waypoints[-1].time_s)
    assert touched, "демо задумано так, что под временной зоной есть что снимать"


def test_router_goes_around_a_zone() -> None:
    """E5: перелёт обходит зону, а не режет её."""
    scenario = small_scenario(no_fly_zones=[{"id": "wall", "polygon": [_WALL]}])
    work_area = build_work_area(scenario)
    start = work_area.projector.to_metric(
        scenario.sites[0].position.model_copy(update={"lon": 30.300})
    )
    end = work_area.projector.to_metric(
        scenario.sites[0].position.model_copy(update={"lon": 30.320})
    )
    route = make_router(work_area)(start, end, 300.0)
    line = LineString([(p.x_m, p.y_m) for p in route.points])
    assert len(route.points) > 2
    assert not line.intersects(work_area.zones[0].inner)


def test_router_flies_over_a_low_zone() -> None:
    """Зона до 120 м не мешает перелёту на 300 м — путь прямой (№40)."""
    scenario = small_scenario(no_fly_zones=[{"id": "wall", "polygon": [_WALL], "max_alt_m": 120}])
    work_area = build_work_area(scenario)
    start = MetricPoint(
        x_m=work_area.zones[0].footprint.bounds[0] - 500,
        y_m=work_area.zones[0].footprint.centroid.y,
    )
    end = MetricPoint(
        x_m=work_area.zones[0].footprint.bounds[2] + 500,
        y_m=work_area.zones[0].footprint.centroid.y,
    )
    assert len(make_router(work_area)(start, end, 300.0).points) == 2


def test_router_avoids_tall_obstacle_only_below_its_safe_altitude() -> None:
    """Мачта 250 м: на 250 м её обходят, на 400 м пролетают над ней."""
    scenario = small_scenario(
        obstacles=[{"id": "mast", "position": [30.31, 59.905], "height_m": 250}]
    )
    work_area = build_work_area(scenario)
    mast = work_area.obstacles[0].footprint
    start = MetricPoint(x_m=mast.x - 800, y_m=mast.y)
    end = MetricPoint(x_m=mast.x + 800, y_m=mast.y)
    router = make_router(work_area)
    assert len(router(start, end, 250.0).points) > 2
    assert len(router(start, end, 400.0).points) == 2
