"""Разведение бортов (E14) и обход зон (E5): эшелоны, расписание, граф видимости."""

from __future__ import annotations

from shapely.geometry import LineString, box

from core.assumptions import VERTICAL_SEPARATION_M
from core.flight.maneuver import ManeuverWindow, maneuver_overlap
from core.geo.mask import build_work_area
from core.pipeline import PlanningRun
from core.schemas import FlightPhase, MetricPoint
from core.transit.routing import make_router
from tests.conftest import small_scenario

_WALL = [[30.305, 59.880], [30.315, 59.880], [30.315, 59.920], [30.305, 59.920]]


def _levels(run: PlanningRun) -> dict[tuple[str, int], float]:
    """Эшелон перелёта по каждому борту плана."""
    return {
        (sortie.vehicle_id, sortie.vehicle_instance): sortie.transit_altitude_m
        for sortie in run.mission.sorties
    }


def test_each_vehicle_gets_its_own_transit_level(runs: dict[str, PlanningRun]) -> None:
    """№10: пока эшелонов под потолком борта хватает, у каждого борта — свой."""
    run = runs["demo_100km2"]
    levels = _levels(run)
    unrestricted = {
        key: level
        for key, level in levels.items()
        if run.vehicles[key[0]].max_altitude_agl_m is None
    }
    assert len(unrestricted) >= 2
    assert len(set(unrestricted.values())) == len(unrestricted)


def test_transit_level_follows_own_survey_altitude(runs: dict[str, PlanningRun]) -> None:
    """Долг №34: эшелон борта — над его съёмкой, а не над съёмкой всего флота.

    В демо самолёт снимает на ~456 м, коптеры — на ~255 м. Раньше коптеров
    гнали на перелёт выше самолётной съёмки (486 м), и набор со снижением
    съедали до трети вылета. Теперь коптер перелетает чуть выше своей съёмки.
    """
    run = runs["demo_100km2"]
    altitudes = {
        (sortie.vehicle_id, sortie.vehicle_instance): sortie.survey_altitude_m
        for sortie in run.mission.sorties
    }
    highest_survey = max(altitudes.values())
    low = [key for key, altitude in altitudes.items() if altitude < highest_survey]
    assert low, "в демо есть борта, снимающие ниже самого высокого"
    for key in low:
        assert _levels(run)[key] < highest_survey


def test_transit_levels_keep_vertical_separation(runs: dict[str, PlanningRun]) -> None:
    """Эшелон отстоит от чужой съёмки и от чужого эшелона на интервал.

    Иначе борт на перелёте прошёл бы вплотную к чужой съёмке или к чужому
    эшелону. Совпадение эшелонов допустимо — это объявленный делёж, который
    разводит расписание.
    """
    for run in runs.values():
        if not run.mission.sorties:
            continue
        levels = _levels(run)
        altitudes = {sortie.survey_altitude_m for sortie in run.mission.sorties}
        for key, level in levels.items():
            own = next(
                sortie.survey_altitude_m
                for sortie in run.mission.sorties
                if (sortie.vehicle_id, sortie.vehicle_instance) == key
            )
            for altitude in altitudes:
                if altitude == own and level == own:
                    continue  # эшелона не нашлось — перелёт на высоте съёмки
                assert abs(level - altitude) >= VERTICAL_SEPARATION_M - 1e-6
            for other_key, other in levels.items():
                if other_key == key or other == level:
                    continue
                assert abs(level - other) >= VERTICAL_SEPARATION_M - 1e-6


def test_shared_level_warning_names_the_boards(runs: dict[str, PlanningRun]) -> None:
    """Долг №35: делёж эшелона объявляется по факту — по предупреждению на эшелон.

    Раньше предупреждение считало эшелоны коридора («8 эшелонов на 9 бортов»),
    хотя борта делили эшелон из-за своего потолка.
    """
    run = runs["demo_100km2"]
    boards: dict[float, set[str]] = {}
    for sortie in run.mission.sorties:
        board = f"{sortie.vehicle_id}_{sortie.vehicle_instance + 1}"
        boards.setdefault(round(sortie.transit_altitude_m), set()).add(board)
    shared = {level: names for level, names in boards.items() if len(names) > 1}
    assert shared, "в демо Gemini делят эшелон — предупреждению есть что сказать"
    warned = {
        int(w.context["level_m"]): set(w.context["board_ids"].split(","))
        for w in run.result.warnings()
        if w.code == "deconflict.levels_shared"
    }
    assert warned == shared


def _window(x_m: float, altitude_m: float, from_s: float, to_s: float) -> ManeuverWindow:
    """Квадратная зона манёвра 200 × 200 м с центром в (x_m, 0)."""
    return ManeuverWindow(
        sortie_id=f"s{x_m:.0f}",
        vehicle_id="plane",
        cell_id="c1",
        footprint=box(x_m - 100.0, -100.0, x_m + 100.0, 100.0),
        altitude_m=altitude_m,
        from_s=from_s,
        to_s=to_s,
        separation_m=300.0,
    )


def test_maneuver_zones_conflict_only_when_time_and_height_overlap() -> None:
    """№37, №38: зоны манёвра опасны вместе — в общее время и на близких высотах."""
    first = _window(0.0, 200.0, 0.0, 600.0)
    near = _window(350.0, 200.0, 300.0, 900.0)  # 150 м между зонами при минимуме 300
    assert maneuver_overlap(first, near) is not None
    assert maneuver_overlap(first, _window(350.0, 200.0, 700.0, 900.0)) is None
    assert maneuver_overlap(first, _window(350.0, 240.0, 300.0, 900.0)) is None
    assert maneuver_overlap(first, _window(1000.0, 200.0, 300.0, 900.0)) is None


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
