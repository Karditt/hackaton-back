"""Срок плана: порядок облёта, переходы на высоте съёмки, набор на ходу, зоны площадок.

Долги №56–№60 (docs/current_state.md, ревью 2026-09-28): на данных заказчика
самолёт половину вылета тратил на подъёмы на эшелон между островками, набор
высоты «на месте» и перелёты через всю область к чужим площадкам.
"""

from __future__ import annotations

import math

import pytest

from core import assumptions
from core.allocate.order import cheapest_insertion, improve_order
from core.catalog import VehicleCatalog
from core.deconflict.hops import SlopeClearance, SurveyHopPolicy
from core.flight.cost import build_sortie, pass_directions
from core.geo.mask import build_work_area
from core.pipeline import run_pipeline
from core.schemas import (
    Cell,
    CellCoverage,
    FlightPhase,
    MetricLineString,
    MetricPoint,
    MetricPolygon,
    MissionStatus,
    Strip,
    SurveyParams,
    SurveyPlan,
)
from tests.conftest import small_scenario


def _p(x: float, y: float) -> MetricPoint:
    return MetricPoint(x_m=x, y_m=y)


def _tour(
    ids: list[str], ends: dict[str, tuple[MetricPoint, MetricPoint]], site: MetricPoint
) -> float:
    return pass_directions([ends[item] for item in ids], site, site)[1]


# --- порядок облёта --------------------------------------------------------


def test_improve_order_never_loses_cells_or_gets_longer() -> None:
    """Перестановка — те же подобласти, связки не длиннее."""
    ends = {}
    ids = []
    for index in range(12):
        x = (index * 7919) % 13 * 1000.0
        y = (index * 104729) % 11 * 1000.0
        ids.append(f"c{index}")
        ends[f"c{index}"] = (_p(x, y), _p(x + 300.0, y + 150.0))
    site = _p(0.0, -2000.0)
    order = improve_order(ids, ends, site, site)
    assert sorted(order) == sorted(ids)
    assert _tour(order, ends, site) <= _tour(ids, ends, site) + 1e-6
    assert _tour(order, ends, site) < 0.9 * _tour(ids, ends, site)


def test_improve_order_untangles_a_zigzag() -> None:
    """Подобласти на одной линии, облёт «туда-сюда» — после перестановки по порядку."""
    ends = {f"c{i}": (_p(i * 1000.0, 0.0), _p(i * 1000.0 + 200.0, 0.0)) for i in range(6)}
    zigzag = ["c0", "c5", "c1", "c4", "c2", "c3"]
    site = _p(-1000.0, 0.0)
    order = improve_order(zigzag, ends, site, site)
    straight = ["c0", "c1", "c2", "c3", "c4", "c5"]
    assert _tour(order, ends, site) == pytest.approx(_tour(straight, ends, site))


def test_cheapest_insertion_goes_between_neighbours() -> None:
    ends = {f"c{i}": (_p(i * 1000.0, 0.0), _p(i * 1000.0 + 200.0, 0.0)) for i in range(4)}
    site = _p(-1000.0, 0.0)
    order = cheapest_insertion(["c0", "c1", "c3"], "c2", ends, site, _p(5000.0, 0.0))
    assert order == ["c0", "c1", "c2", "c3"]


def test_pass_directions_reverses_a_cell_when_shorter() -> None:
    """Змейку, у которой выход ближе к площадке взлёта, выгоднее пройти задом наперёд."""
    far_entry = (_p(5000.0, 0.0), _p(1000.0, 0.0))
    flags, length_m = pass_directions([far_entry], _p(0.0, 0.0), _p(6000.0, 0.0))
    assert flags == [True]
    assert length_m == pytest.approx(1000.0 + 1000.0)


# --- модель вылета: переходы и набор высоты ------------------------------


def _params(vehicle_id: str, altitude_m: float, speed_mps: float) -> SurveyParams:
    return SurveyParams(
        vehicle_id=vehicle_id,
        payload_id="probe",
        survey_type="rgb",
        altitude_agl_m=altitude_m,
        gsd_m=0.05,
        swath_width_m=300.0,
        strip_spacing_m=150.0,
        survey_speed_mps=speed_mps,
        transit_speed_mps=speed_mps,
        forward_overlap=0.7,
        side_overlap=0.5,
    )


def _cell(
    cell_id: str, vehicle_id: str, x0: float, x1: float, y: float, alt: float
) -> CellCoverage:
    strip = Strip(
        id=f"{cell_id}:s0",
        cell_id=cell_id,
        vehicle_id=vehicle_id,
        line=MetricLineString(points=[_p(x0, y), _p(x1, y)]),
        length_m=abs(x1 - x0),
        altitude_agl_m=alt,
        speed_mps=10.0,
        azimuth_deg=90.0,
    )
    return CellCoverage(
        cell_id=cell_id,
        vehicle_id=vehicle_id,
        strips=[strip],
        covered_area_m2=1.0,
        serviceable=True,
    )


def _straight(start: MetricPoint, end: MetricPoint, _altitude_m: float) -> MetricLineString:
    return MetricLineString(points=[start, end])


def test_hop_between_islands_stays_on_survey_level(catalog: VehicleCatalog) -> None:
    """Долг №57: разрешённый переход между островками — без подъёма на эшелон."""
    vehicle = catalog.get("geoscan-gemini")
    params = _params(vehicle.id, 250.0, 10.0)
    cells = [
        _cell("a", vehicle.id, 1000.0, 2000.0, 0.0, 250.0),
        _cell("b", vehicle.id, 3000.0, 4000.0, 0.0, 250.0),
    ]
    site = _p(0.0, 0.0)

    def sortie(allowed: bool):
        return build_sortie(
            "t",
            vehicle,
            params,
            "s",
            site,
            "s",
            site,
            cells,
            400.0,
            _straight,
            own_area=None,
            survey_hop=(lambda _line: allowed),
        )

    low, high = sortie(True), sortie(False)
    climbs_low = sum(leg.phase is FlightPhase.CLIMB for leg in low.legs)
    climbs_high = sum(leg.phase is FlightPhase.CLIMB for leg in high.legs)
    assert climbs_low == climbs_high - 1
    assert low.duration_s < high.duration_s


def test_fixed_wing_climbs_on_the_move(catalog: VehicleCatalog) -> None:
    """Долг №56: самолёт набирает и теряет высоту на ходу, с той же вертикальной скоростью."""
    vehicle = catalog.get("geoscan-201")
    assert vehicle.is_fixed_wing()
    params = _params(vehicle.id, 450.0, 27.0)
    cells = [_cell("a", vehicle.id, 20000.0, 22000.0, 0.0, 450.0)]
    site = _p(0.0, 0.0)

    def sortie(check):
        return build_sortie(
            "t",
            vehicle,
            params,
            "s",
            site,
            "s",
            site,
            cells,
            540.0,
            _straight,
            slope_check=check,
        )

    moving = sortie(lambda _points, _low, _high: True)
    hovering = sortie(lambda _points, _low, _high: False)
    vertical = [leg for leg in moving.legs if leg.phase in (FlightPhase.CLIMB, FlightPhase.DESCENT)]
    assert vertical and all(leg.ground_speed_mps > 0.0 for leg in vertical)
    for leg in vertical:
        rise = abs(leg.waypoints[-1].alt_m - leg.waypoints[0].alt_m)
        rate = (
            assumptions.CLIMB_RATE_MPS
            if leg.phase is FlightPhase.CLIMB
            else assumptions.DESCENT_RATE_MPS
        )
        assert rise <= assumptions.SLOPE_LEG_STEP_M + 1e-6
        assert leg.duration_s == pytest.approx(rise / rate)
    assert moving.duration_s < hovering.duration_s
    assert moving.distance_m == pytest.approx(hovering.distance_m)
    # Отказ проверки — набор на месте, как у мультиротора.
    assert all(
        leg.ground_speed_mps == 0.0
        for leg in hovering.legs
        if leg.phase in (FlightPhase.CLIMB, FlightPhase.DESCENT)
    )


def test_short_hop_keeps_hovering_climb(catalog: VehicleCatalog) -> None:
    """Перелёт короче набора и снижения — высота набирается на месте."""
    vehicle = catalog.get("geoscan-201")
    params = _params(vehicle.id, 450.0, 27.0)
    cells = [_cell("a", vehicle.id, 100.0, 400.0, 0.0, 450.0)]
    site = _p(0.0, 0.0)
    sortie = build_sortie(
        "t",
        vehicle,
        params,
        "s",
        site,
        "s",
        site,
        cells,
        540.0,
        _straight,
        slope_check=lambda _points, _low, _high: True,
    )
    assert any(
        leg.phase is FlightPhase.CLIMB and leg.ground_speed_mps == 0.0 for leg in sortie.legs
    )


# --- правила переходов ------------------------------------------------------


def _square(cell_id: str, x: float, y: float, side: float = 200.0) -> Cell:
    return Cell(
        id=cell_id,
        polygon=MetricPolygon(
            outer=[_p(x, y), _p(x + side, y), _p(x + side, y + side), _p(x, y + side)]
        ),
        area_m2=side * side,
        centroid=_p(x + side / 2, y + side / 2),
        row=0,
        col=0,
    )


def test_survey_hop_keeps_away_from_foreign_cells(catalog: VehicleCatalog) -> None:
    """Переход на высоте съёмки не подходит к чужой подобласти ближе отступа.

    Отступ — зона манёвра самолёта на той же высоте плюс минимум разделения:
    так переход не заходит туда, где на той же высоте может разворачиваться
    чужой борт.
    """
    scenario = small_scenario()
    work_area = build_work_area(scenario)
    vehicle = catalog.get("geoscan-gemini")
    plan = SurveyPlan(per_vehicle=[_params(vehicle.id, 250.0, 10.0)])
    own_a, own_b = _square("a", 0.0, 0.0), _square("b", 3000.0, 0.0)
    near = _square("near", 1500.0, 130.0)
    far = _square("far", 1500.0, 2000.0)
    for foreign, allowed in ((near, False), (far, True)):
        policy = SurveyHopPolicy([own_a, own_b, foreign], plan, {vehicle.id: vehicle}, work_area)
        assert policy.clearance_m(vehicle.id) == pytest.approx(
            assumptions.SEPARATION_MULTIROTOR_M + assumptions.BOUNDARY_TOLERANCE_M
        )
        check = policy.check(vehicle.id, {"a", "b"}, _union(own_a, own_b))
        line = MetricLineString(points=[_p(200.0, 100.0), _p(3000.0, 100.0)])
        assert check(line) is allowed


def _union(*cells: Cell):
    from shapely.ops import unary_union

    from core.geo.project import polygon_to_shapely

    return unary_union([polygon_to_shapely(cell.polygon) for cell in cells])


def test_slope_clearance_sees_low_obstacles(catalog: VehicleCatalog) -> None:
    """Наклонный кусок проверяется по нижней высоте: низкое препятствие мешает набору."""
    scenario = small_scenario(
        obstacles=[{"id": "mast", "position": [30.310, 59.902], "height_m": 120.0}]
    )
    work_area = build_work_area(scenario)
    clearance = SlopeClearance(work_area)
    mast = work_area.obstacles[0].inner.centroid
    through = [_p(mast.x - 500.0, mast.y), _p(mast.x + 500.0, mast.y)]
    assert clearance(through, 0.0, 30.0) is False
    assert clearance(through, 400.0, 430.0) is True


# --- зоны площадок ----------------------------------------------------------


def test_fixed_wings_split_between_sites_by_work(catalog: VehicleCatalog) -> None:
    """Долг №60: самолёты делятся между площадками по работе, а не встают все на одну.

    Полоса 30 км с площадками на концах, работа поровну: два самолёта без
    приписки — по одному на площадку, и каждый снимает свою половину.
    """
    scenario = small_scenario(
        area=[[[30.300, 59.900], [30.840, 59.900], [30.840, 59.912], [30.300, 59.912]]],
        sites=[
            {"id": "west", "position": [30.298, 59.898]},
            {"id": "east", "position": [30.842, 59.898]},
        ],
        fleet=[{"vehicle_id": "geoscan-201", "count": 2}],
    )
    run = run_pipeline(scenario, catalog=catalog)
    assert run.mission.status is MissionStatus.FEASIBLE, [r.message for r in run.mission.reasons]
    sites = {sortie.vehicle_instance: sortie.takeoff_site_id for sortie in run.mission.sorties}
    assert set(sites.values()) == {"west", "east"}
    middle_x = sum(site.x_m for site in run.work_area.sites.values()) / 2
    for sortie in run.mission.sorties:
        home_x = run.work_area.site(sortie.takeoff_site_id).x_m
        xs = [leg.waypoints[0].x_m for leg in sortie.legs if leg.phase is FlightPhase.STRIP]
        mean = sum(xs) / len(xs)
        assert math.copysign(1.0, mean - middle_x) == math.copysign(1.0, home_x - middle_x)
