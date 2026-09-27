"""Нарезка по островкам и галсы подобласти (E3, долги №46, №51)."""

from __future__ import annotations

import math

import pytest
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from core import assumptions
from core.catalog import VehicleCatalog
from core.coverage.decompose import split_island
from core.coverage.oracle import CoverageOracle, fit_strips
from core.coverage.strips import candidate_azimuths, cover_cell, maneuver_zones
from core.geo.project import polygon_to_shapely
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import SurveyParams, SurveyType
from tests.conftest import small_scenario


def _params(vehicle_id: str, spacing_m: float = 150.0) -> SurveyParams:
    """Параметры съёмки для геометрических проверок: важен только шаг."""
    return SurveyParams(
        vehicle_id=vehicle_id,
        payload_id="probe",
        survey_type=SurveyType.RGB,
        altitude_agl_m=300.0,
        gsd_m=0.05,
        swath_width_m=spacing_m * 2.0,
        strip_spacing_m=spacing_m,
        survey_speed_mps=20.0,
        transit_speed_mps=20.0,
        forward_overlap=0.7,
        side_overlap=0.5,
    )


def _line(strip) -> LineString:
    return LineString([(point.x_m, point.y_m) for point in strip.line.points])


def test_strips_stay_inside_a_cell_with_a_needle() -> None:
    """Галсы не выходят из подобласти ни при каком направлении.

    Повёрнутый полигон с узким выступом из-за округления становился
    самопересекающимся, и пересечение с ним давало галсы вне подобласти — в
    запретной зоне (нашёл краш-тест на `real_nw`). Теперь поворачивается резак.
    """
    needle = Polygon(
        [(0, 0), (900, 0), (900, 700), (452, 700), (450.0000001, 2600), (448, 700), (0, 700)]
    )
    params = _params("geoscan-gemini")
    for azimuth in range(0, 180, 7):
        for strip in cover_cell("needle", needle, params, float(azimuth)):
            assert _line(strip).within(needle.buffer(assumptions.GEOMETRY_TOLERANCE_M))


def test_turn_zone_bulges_outward_only_by_the_loop(catalog: VehicleCatalog) -> None:
    """Разворот идёт к соседнему галсу: наружу зона выходит на R − d/2, а не на 2R."""
    plane = catalog.get("geoscan-201")
    spacing = 1.6 * plane.turn_radius_m
    cell = box(0, 0, 2000, 1200)
    strips = cover_cell("cell", cell, _params(plane.id, spacing), 90.0)
    zones = unary_union([polygon_to_shapely(zone) for zone in maneuver_zones(strips, plane)])
    first_y = min(point.y_m for strip in strips for point in strip.line.points)
    bulge = plane.turn_radius_m - spacing / 2.0
    assert zones.bounds[1] >= first_y - bulge - 1e-6
    full_reach = assumptions.MANEUVER_ZONE_RADIUS_FACTOR * plane.turn_radius_m
    assert zones.bounds[1] > first_y - full_reach + 1.0


def test_cell_along_a_zone_is_flown_by_a_plane(catalog: VehicleCatalog) -> None:
    """Долг №51: подобласть вдоль запретной зоны самолёту доступна — галсы вдоль границы."""
    plane = catalog.get("geoscan-201")
    params = _params(plane.id, 1.6 * plane.turn_radius_m)
    zone = box(-3000, -1000, 5000, 0)
    cell = Polygon([(0, 0), (2000, 0), (2600, 1500), (0, 1500)])
    strips, zones, problem = fit_strips("cell", cell, params, plane, zone, None)
    assert problem is None
    assert all(abs(strip.azimuth_deg % 180.0 - 90.0) < 1e-6 for strip in strips)


def test_candidates_start_with_the_fewest_strips() -> None:
    """Первое направление — вдоль длинной стороны: меньше всего галсов и разворотов."""
    thin = Polygon([(0, 0), (3000, 3000), (3100, 2900), (100, -100)])
    first = candidate_azimuths(thin, 100.0)[0]
    assert math.isclose(first, 45.0, abs_tol=1e-6)


def test_big_island_is_split_evenly_and_exactly() -> None:
    """Крупный островок режется на части не больше ячейки, вместе — ровно он."""
    island = Polygon(
        [(0, 0), (5000, 800), (4600, 3100), (-300, 2400)],
        [[(1000, 1000), (1400, 1000), (1400, 1400), (1000, 1400)]],
    )
    size = 1000.0
    parts = split_island(island, size)
    assert len(parts) > 1
    assert sum(part.area for part in parts) == pytest.approx(island.area, rel=1e-9)
    assert unary_union(parts).area == pytest.approx(island.area, rel=1e-9)
    limit = size**2 * (1.0 + assumptions.CELL_MERGE_FRACTION) * 1.5
    assert all(part.area <= limit for part in parts)


def test_decomposition_covers_the_mask_exactly(run: PlanningRun) -> None:
    """Подобласти вместе — ровно маска съёмки, без наложений и дыр."""
    decomposition = run.result.decomposition
    if decomposition is None:
        return
    shapes = [polygon_to_shapely(cell.polygon) for cell in decomposition.cells]
    total = sum(shape.area for shape in shapes)
    assert total == pytest.approx(decomposition.work_area_m2, rel=1e-6)
    assert unary_union(shapes).area == pytest.approx(total, rel=1e-6)


def test_small_islands_are_not_cut(runs: dict[str, PlanningRun]) -> None:
    """Островок меньше ячейки — одна подобласть: сетка его не режет (долг №46)."""
    run = runs["demo_100km2"]
    decomposition = run.result.decomposition
    assert decomposition is not None
    lake_side = [
        cell for cell in decomposition.cells if cell.area_m2 <= decomposition.cell_size_m**2
    ]
    assert lake_side, "в демо есть отдельный участок меньше ячейки"
    assert decomposition.stub is False


def test_island_hugging_a_zone_is_carved_for_the_plane(catalog: VehicleCatalog) -> None:
    """Долг №51: подобласть с вырезом под круглую зону самолёт снимает, кроме полосы у зоны.

    Мультироторов во флоте нет, поэтому полосу у зоны снять некому — задание
    невыполнимо. Но недоступна самолёту только полоса, а не весь участок.
    """
    circle = Point(30.320, 59.905).buffer(0.004, quad_segs=16)
    zone = {
        "id": "round",
        "polygon": [[[round(x, 6), round(y, 6)] for x, y in circle.exterior.coords]],
    }
    scenario = small_scenario(
        no_fly_zones=[zone],
        fleet=[{"vehicle_id": "geoscan-201", "count": 1, "payload_id": "riebo-r4"}],
    )
    run = run_pipeline(scenario, catalog=catalog)
    decomposition = run.result.decomposition
    assert decomposition is not None and len(decomposition.cells) > 1
    oracle = CoverageOracle(decomposition, run.result.survey_plan, run.vehicles, run.work_area)
    flown = sum(
        cell.area_m2 for cell in decomposition.cells if oracle.serviceable(cell.id, "geoscan-201")
    )
    assert flown > 0.5 * decomposition.work_area_m2
