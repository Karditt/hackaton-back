"""Разворот самолёта, облёт «через галс», скорость по оси при ветре, набор «по спирали».

Долги №52 и №61 (docs/dev/current_state.md): ветер не входил в выбор направления
галсов, а разворот на соседний галс при шаге меньше двух радиусов считался
полуокружностью — короче, чем самолёт может.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import LineString, Polygon, box

from core import assumptions
from core.allocate.greedy import _sized_cap
from core.catalog import VehicleCatalog
from core.coverage.oracle import fit_strips, strip_interlace
from core.coverage.strips import cover_cell
from core.flight.cost import (
    build_sortie,
    interlace_step,
    loop_length_m,
    strip_heading,
    turn_length_m,
)
from core.flight.wind import required_airspeed_mps
from core.pipeline import PlanningRun
from core.schemas import (
    CellCoverage,
    FlightPhase,
    MetricLineString,
    MetricPoint,
    Strip,
    SurveyParams,
    Wind,
)
from core.survey.plan import axis_survey_speed_mps, build_survey_plan
from tests.conftest import small_scenario


def _p(x: float, y: float) -> MetricPoint:
    return MetricPoint(x_m=x, y_m=y)


# --- петля разворота -------------------------------------------------------


def test_loop_is_semicircle_at_two_radii_and_longer_below() -> None:
    """При шаге 2R — полуокружность, меньше — петля «омега», на ту же линию — 7πR/3."""
    radius = 128.0
    assert loop_length_m(radius, 2 * radius) == pytest.approx(math.pi * radius)
    assert loop_length_m(radius, 3 * radius) == pytest.approx(math.pi * radius + radius)
    assert loop_length_m(radius, 0.0) == pytest.approx(7 * math.pi * radius / 3)
    lengths = [loop_length_m(radius, d) for d in (0.0, 50.0, 100.0, 200.0, 256.0)]
    assert lengths == sorted(lengths, reverse=True)
    # Сдвиг вдоль курса проходится по прямой.
    assert loop_length_m(radius, 300.0, 40.0) == pytest.approx(loop_length_m(radius, 300.0) + 40.0)


def test_turn_to_next_piece_of_same_line_is_straight(catalog: VehicleCatalog) -> None:
    """Кусок того же галса за отверстием — по прямой, встречный соседний — петлёй."""
    plane = catalog.get("geoscan-201")
    east = (1.0, 0.0)
    west = (-1.0, 0.0)
    same_line = turn_length_m(plane, _p(0.0, 0.0), _p(300.0, 0.0), east, east)
    assert same_line == pytest.approx(300.0)
    neighbour = turn_length_m(plane, _p(0.0, 0.0), _p(0.0, 205.0), east, west)
    assert neighbour == pytest.approx(loop_length_m(plane.turn_radius_m, 205.0))
    assert neighbour > math.pi * plane.turn_radius_m


def test_interlace_only_where_it_is_much_shorter() -> None:
    """Через галс — когда переход через k линий заметно короче петли на соседний."""
    radius = 128.3
    assert interlace_step(radius, 300.0) == 1  # шаг шире 2R — обычная змейка
    assert interlace_step(radius, 205.4) == 1  # демо и `real_msk`: выигрыш 12% — мало
    assert interlace_step(radius, 131.5) == 2  # `medium`: выигрыш 47%
    assert interlace_step(radius, 60.0) >= 3
    assert interlace_step(radius, 250.0) == 1  # у самого 2R «омега» короче
    assert interlace_step(0.0, 50.0) == 1  # мультиротор


def _params(vehicle_id: str, spacing_m: float, speed_mps: float = 27.0) -> SurveyParams:
    return SurveyParams(
        vehicle_id=vehicle_id,
        payload_id="probe",
        survey_type="rgb",
        altitude_agl_m=450.0,
        gsd_m=0.05,
        swath_width_m=2 * spacing_m,
        strip_spacing_m=spacing_m,
        survey_speed_mps=speed_mps,
        transit_speed_mps=speed_mps,
        forward_overlap=0.7,
        side_overlap=0.5,
    )


def test_interlaced_cover_visits_every_line_once_alternating() -> None:
    """Облёт через галс: все линии по разу, серии туда и обратно, курс чередуется."""
    cell = box(0.0, 0.0, 2000.0, 2050.0)
    params = _params("plane", 205.0)
    plain = cover_cell("c", cell, params, 90.0)
    interlaced = cover_cell("c", cell, params, 90.0, interlace=2)
    assert sorted(strip.id for strip in plain) == sorted(strip.id for strip in interlaced)
    lines = [int(strip.id.split(":s")[1].split("-")[0]) for strip in interlaced]
    count = len(lines)
    assert lines == [*range(0, count, 2), *reversed(range(1, count, 2))]
    headings = [strip_heading(strip) for strip in interlaced]
    for before, after in zip(headings, headings[1:], strict=False):
        assert before is not None and after is not None
        assert before[0] * after[0] + before[1] * after[1] < 0.0
    # Переход внутри серии — через линию, шире двух радиусов 201.
    gaps = [
        abs(after.start().y_m - before.end().y_m)
        for before, after in zip(interlaced, interlaced[1:], strict=False)
    ]
    assert sum(gap > 400.0 for gap in gaps) == count - 2


def test_interlaced_turns_are_shorter_in_sortie(catalog: VehicleCatalog) -> None:
    """В модели вылета облёт через галс даёт развороты короче «омеги»."""
    plane = catalog.get("geoscan-201")
    params = _params(plane.id, 131.5)
    cell = box(3000.0, 0.0, 6000.0, 1315.0)

    def turns(interlace: int) -> float:
        strips = cover_cell("c", cell, params, 90.0, interlace=interlace)
        coverage = CellCoverage(
            cell_id="c", vehicle_id=plane.id, strips=strips, covered_area_m2=1.0, serviceable=True
        )
        sortie = build_sortie(
            "t",
            plane,
            params,
            "s",
            _p(0.0, 0.0),
            "s",
            _p(0.0, 0.0),
            [coverage],
            500.0,
            lambda start, end, _alt: MetricLineString(points=[start, end]),
        )
        return sum(leg.duration_s for leg in sortie.legs if leg.phase is FlightPhase.TURN)

    assert strip_interlace(params, plane) == 2
    assert turns(2) < 0.7 * turns(1)


# --- скорость по оси при ветре ---------------------------------------------


def test_crosswind_axis_is_faster_than_any_course(catalog: VehicleCatalog) -> None:
    """Долг №52: поперёк ветра Gemini держит крейсерскую, вдоль — меньше (как на любой курс)."""
    gemini = catalog.get("geoscan-gemini")
    wind = Wind(speed_mps=7.0, direction_deg=0.0)
    scenario = small_scenario(
        wind={"speed_mps": 7.0, "direction_deg": 0.0},
        fleet=[{"vehicle_id": gemini.id, "count": 1, "payload_id": "geoscan-pf1b"}],
    )
    params = build_survey_plan(scenario, {gemini.id: gemini}).per_vehicle[0]
    assert params.survey_speed_mps < gemini.cruise_speed_mps
    across = axis_survey_speed_mps(gemini, params, wind, 90.0)
    along = axis_survey_speed_mps(gemini, params, wind, 0.0)
    assert across == pytest.approx(gemini.cruise_speed_mps)
    assert along == pytest.approx(params.survey_speed_mps)
    top = gemini.max_speed_mps or gemini.cruise_speed_mps
    for heading in (90.0, 270.0):
        assert required_airspeed_mps(heading, across, wind) <= top + 1e-9
    calm = Wind(speed_mps=0.0, direction_deg=0.0)
    assert axis_survey_speed_mps(gemini, params, calm, 37.0) == pytest.approx(
        gemini.cruise_speed_mps
    )


def test_wind_turns_strips_across_it(catalog: VehicleCatalog) -> None:
    """Квадрат без ветра — любое направление; при ветре с севера галсы ложатся поперёк."""
    gemini = catalog.get("geoscan-gemini")
    scenario = small_scenario(
        wind={"speed_mps": 7.0, "direction_deg": 0.0},
        fleet=[{"vehicle_id": gemini.id, "count": 1, "payload_id": "geoscan-pf1b"}],
    )
    params = build_survey_plan(scenario, {gemini.id: gemini}).per_vehicle[0]
    cell = box(0.0, 0.0, 1500.0, 1500.0)
    strips, _zones, problem = fit_strips(
        "c", cell, params, gemini, Polygon(), None, Wind(speed_mps=7.0, direction_deg=0.0)
    )
    assert problem is None
    assert all(abs(math.sin(math.radians(strip.azimuth_deg))) > 0.9 for strip in strips)
    assert all(strip.speed_mps == pytest.approx(gemini.cruise_speed_mps) for strip in strips)


# --- набор «по спирали» и нарезка вылетов ------------------------------------


def test_short_hop_climbs_while_moving_as_far_as_it_can(catalog: VehicleCatalog) -> None:
    """Короткий переход: время — max(длина / скорость, вертикаль), а не сумма."""
    plane = catalog.get("geoscan-201")
    params = _params(plane.id, 205.0)
    strip = Strip(
        id="a:s0",
        cell_id="a",
        vehicle_id=plane.id,
        line=MetricLineString(points=[_p(1500.0, 0.0), _p(4000.0, 0.0)]),
        length_m=2500.0,
        altitude_agl_m=450.0,
        speed_mps=27.0,
        azimuth_deg=90.0,
    )
    cell = CellCoverage(
        cell_id="a", vehicle_id=plane.id, strips=[strip], covered_area_m2=1.0, serviceable=True
    )

    def sortie(check):
        return build_sortie(
            "t",
            plane,
            params,
            "s",
            _p(0.0, 0.0),
            "s",
            _p(0.0, 0.0),
            [cell],
            600.0,
            lambda start, end, _alt: MetricLineString(points=[start, end]),
            slope_check=check,
        )

    moving = sortie(lambda _points, _low, _high: True)
    hovering = sortie(lambda _points, _low, _high: False)
    first = [leg for leg in moving.legs if leg.phase is not FlightPhase.TAKEOFF]
    # Подлёт 1500 м на 27 м/с — 56 с, набор 600 м и снижение 150 м — 260 с.
    climb_s = 600.0 / assumptions.CLIMB_RATE_MPS + 150.0 / assumptions.DESCENT_RATE_MPS
    approach_s = 0.0
    for leg in first:
        if leg.phase is FlightPhase.STRIP:
            break
        approach_s += leg.duration_s
    assert approach_s == pytest.approx(max(1500.0 / 27.0, climb_s))
    assert moving.duration_s < hovering.duration_s


def test_urgent_work_is_flown_before_temporary_zone_opens(runs: dict[str, PlanningRun]) -> None:
    """Демо: зона ОРВД с 1 до 3 ч — работу под ней эвристика снимает до открытия.

    Иначе эту работу пришлось бы ждать до закрытия зоны, и план кончился бы не
    раньше 3 ч. Галсы над зоной — либо до её открытия, либо после закрытия.
    """
    run = runs["demo_100km2"]
    zone = next(zone for zone in run.work_area.zones if not zone.permanent)
    assert zone.active_from_s == pytest.approx(3600.0)
    metrics = run.mission.metrics
    assert metrics is not None and metrics.makespan_s < zone.active_to_s
    under = [
        leg
        for sortie in run.mission.sorties
        for leg in sortie.legs
        if leg.phase is FlightPhase.STRIP
        and LineString([(point.x_m, point.y_m) for point in leg.waypoints]).intersects(zone.inner)
    ]
    assert under and all(leg.waypoints[-1].time_s <= zone.active_from_s for leg in under)


def test_sized_cap_splits_horizon_evenly() -> None:
    """Целевой срок 2,7 ч, вылет до 2,24 ч, подготовка 10 мин — два вылета поровну."""
    cap = _sized_cap(2.7 * 3600, 0.08 * 3600, 2.24 * 3600, 600.0)
    assert cap == pytest.approx((2.62 * 3600 - 600.0) / 2)
    # Срок прошёл — предела нет; до срока меньше вылета — один короткий.
    assert _sized_cap(1000.0, 2000.0, 5000.0, 600.0) == 5000.0
    assert _sized_cap(3000.0, 0.0, 5000.0, 600.0) == pytest.approx(3000.0)
