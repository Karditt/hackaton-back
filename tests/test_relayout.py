"""Новая раскладка островков (core/geo/relayout.py): второй сценарий того же рода.

Раскладка обязана сохранить то, что нельзя выдумать, — формы и площади
островков и расстояния внутри групп соседних, — и поменять общую картину.
"""

from __future__ import annotations

import pytest
import shapely
from shapely.geometry import box

from core.errors import GeometryError
from core.geo.crs import pick_working_crs
from core.geo.kml_import import read_area
from core.geo.project import Projector, polygons_of
from core.geo.relayout import _groups, _polygon_to_geo, relayout_area
from core.schemas import GeoMultiPolygon, GeoPoint
from tests.conftest import REPO_ROOT

CENTER = GeoPoint(lon=36.75, lat=56.07)


def _metric(area: GeoMultiPolygon):
    projector = Projector(pick_working_crs([p for poly in area.polygons for p in poly.outer])[0])
    return polygons_of(projector.multipolygon_to_metric(area))


def _source() -> GeoMultiPolygon:
    """Три группы квадратов: пара, тройка и одиночка, в метрах вокруг 55° с.ш."""
    projector = Projector(pick_working_crs([GeoPoint(lon=38.8, lat=55.0)])[0])
    origin = projector.to_metric(GeoPoint(lon=38.8, lat=55.0))
    x, y = origin.x_m, origin.y_m
    squares = [
        box(x, y, x + 300, y + 300),
        box(x + 400, y, x + 700, y + 200),
        box(x + 5000, y + 5000, x + 5400, y + 5400),
        box(x + 5500, y + 5000, x + 5700, y + 5100),
        box(x + 5000, y + 5500, x + 5100, y + 5900),
        box(x - 8000, y + 3000, x - 7000, y + 3500),
    ]
    return GeoMultiPolygon(polygons=[_polygon_to_geo(projector, square) for square in squares])


def test_relayout_keeps_pieces_and_is_reproducible() -> None:
    """Те же куски той же площади; одно зерно — одна раскладка, другое — другая."""
    source = _source()
    first, report = relayout_area(source, CENTER, 20_000, 20_000, seed=3, link_m=500)
    again, _ = relayout_area(source, CENTER, 20_000, 20_000, seed=3, link_m=500)
    other, _ = relayout_area(source, CENTER, 20_000, 20_000, seed=4, link_m=500)
    assert first == again
    assert first != other
    assert report.pieces == 6 and report.groups == 3

    before = sorted(piece.area for piece in _metric(source))
    after = sorted(piece.area for piece in _metric(first))
    assert after == pytest.approx(before, rel=2e-3)


def test_relayout_moves_groups_rigidly_and_keeps_them_apart() -> None:
    """Внутри группы расстояния прежние; группы не сходятся ближе порога."""
    source = _source()
    result, _ = relayout_area(source, CENTER, 20_000, 20_000, seed=5, link_m=500)
    parts = _metric(result)
    assert shapely.union_all(parts).area == pytest.approx(sum(p.area for p in parts), rel=1e-9)
    groups = sorted(sorted(round(parts[i].area) for i in group) for group in _groups(parts, 500))
    source_parts = _metric(source)
    expected = sorted(
        sorted(round(source_parts[i].area) for i in group) for group in _groups(source_parts, 500)
    )
    assert groups == expected


def test_relayout_stays_inside_the_rectangle() -> None:
    """Все куски — внутри нового прямоугольника вокруг центра."""
    result, _ = relayout_area(_source(), CENTER, 20_000, 16_000, seed=6, link_m=500)
    projector = Projector(pick_working_crs([CENTER])[0])
    anchor = projector.to_metric(CENTER)
    frame = box(anchor.x_m - 10_000, anchor.y_m - 8_000, anchor.x_m + 10_000, anchor.y_m + 8_000)
    for polygon in result.polygons:
        piece = projector.polygon_to_metric(polygon)
        assert frame.buffer(1.0).contains(piece)


def test_relayout_refuses_a_rectangle_too_small() -> None:
    """Группа не влезает — отказ с объяснением, а не наложение кусков."""
    with pytest.raises(GeometryError, match="тесен"):
        relayout_area(_source(), CENTER, 1_500, 1_500, seed=1, link_m=500, attempts=50)


def test_customer_islands_relayout_like_real_nw() -> None:
    """Файл заказчика раскладывается так же, как для сценария real_nw."""
    area, _ = read_area(REPO_ROOT / "data" / "samples" / "survey_area_msk.kml")
    result, report = relayout_area(area, CENTER, 60_000, 58_000, seed=1)
    assert report.pieces == len(area.polygons) == 549
    assert report.groups == 158
    assert report.area_km2 == pytest.approx(245.0, abs=0.5)
    assert len(result.polygons) == 549
