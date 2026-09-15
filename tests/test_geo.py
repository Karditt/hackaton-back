"""Инвариант 1: геометрия живёт в метрах, WGS84 — только на границах."""

from __future__ import annotations

import math

import pytest

from core.errors import GeometryError
from core.geo.crs import pick_working_crs, utm_epsg, utm_zone
from core.geo.mask import build_work_area
from core.geo.project import Projector
from core.schemas import GeoPoint, GeoPolygon, Scenario, Severity


def test_utm_zone_by_longitude() -> None:
    """Зона UTM считается по долготе: Санкт-Петербург — 36-я."""
    assert utm_zone(30.3) == 36
    assert utm_zone(-179.9) == 1
    assert utm_zone(179.9) == 60


def test_utm_epsg_depends_on_hemisphere() -> None:
    """Северное полушарие — 326NN, южное — 327NN."""
    assert utm_epsg(30.3, 59.9) == 32636
    assert utm_epsg(30.3, -33.9) == 32736


def test_working_crs_is_projected(simple_scenario: Scenario) -> None:
    """Рабочая проекция обязана быть метрической, иначе инвариант 1 нарушен."""
    crs, origin = pick_working_crs(simple_scenario.area.outer)
    assert crs.epsg == 32636
    assert crs.unit == "m"
    assert 59.0 < origin.lat < 60.0


def test_projection_roundtrip_is_accurate() -> None:
    """Прямое и обратное преобразование возвращают исходную точку."""
    crs, _ = pick_working_crs([GeoPoint(lon=30.3, lat=59.9)])
    projector = Projector(crs)
    source = GeoPoint(lon=30.31234, lat=59.91234)
    restored = projector.to_geo(projector.to_metric(source))
    assert restored.lon == pytest.approx(source.lon, abs=1e-9)
    assert restored.lat == pytest.approx(source.lat, abs=1e-9)


def test_distances_are_metric_not_degrees() -> None:
    """Ровно та ошибка, ради которой существует инвариант 1.

    Один градус широты — примерно 111 км. Если бы расчёт шёл в градусах,
    расстояние получилось бы равным 1.
    """
    crs, _ = pick_working_crs([GeoPoint(lon=30.3, lat=59.9)])
    projector = Projector(crs)
    south = projector.to_metric(GeoPoint(lon=30.3, lat=59.9))
    north = projector.to_metric(GeoPoint(lon=30.3, lat=60.9))
    distance_m = math.hypot(north.x_m - south.x_m, north.y_m - south.y_m)
    assert 110_000 < distance_m < 112_000


def test_geographic_crs_is_rejected() -> None:
    """Попытка работать в EPSG:4326 внутри ядра должна падать."""
    from core.schemas import CrsInfo

    with pytest.raises(GeometryError):
        Projector(CrsInfo(epsg=4326, name="WGS84"))


def test_degenerate_polygon_is_rejected() -> None:
    """Полигон нулевой площади — ошибка геометрии, а не пустой план.

    Три совпадающие точки, а не три точки на одной параллели: параллель в
    UTM — кривая, и «плоский» на широте контур имеет крошечную, но ненулевую
    площадь.
    """
    crs, _ = pick_working_crs([GeoPoint(lon=30.3, lat=59.9)])
    projector = Projector(crs)
    flat = GeoPolygon.model_validate([[[30.3, 59.9], [30.3, 59.9], [30.3, 59.9]]])
    with pytest.raises(GeometryError):
        projector.polygon_to_metric(flat)


def test_no_fly_zones_are_subtracted_with_buffer(scenario: Scenario) -> None:
    """Рабочая маска не больше области, а при наличии запретных зон — строго меньше."""
    work_area = build_work_area(scenario)
    assert work_area.mask_m2 <= work_area.area_m2 + 1.0
    if scenario.no_fly_zones or scenario.airspace_boundary is not None:
        assert work_area.mask_m2 < work_area.area_m2


def test_mask_excludes_holes(scenario: Scenario) -> None:
    """Отверстия в области не попадают в маску."""
    work_area = build_work_area(scenario)
    if scenario.area.holes:
        assert work_area.area_m2 < _outer_area_m2(work_area)


def test_site_inside_nfz_is_reported() -> None:
    """Площадка внутри запретной зоны — предупреждение уровня ОШИБКА."""
    scenario = Scenario.model_validate(
        {
            "id": "site-in-nfz",
            "area": [[[30.30, 59.90], [30.34, 59.90], [30.34, 59.92], [30.30, 59.92]]],
            "no_fly_zones": [
                {
                    "id": "nfz",
                    "polygon": [
                        [[30.31, 59.905], [30.325, 59.905], [30.325, 59.915], [30.31, 59.915]]
                    ],
                }
            ],
            "sites": [{"id": "base", "position": [30.317, 59.910]}],
            "quality": {"gsd_m": 0.05, "forward_overlap": 0.8, "side_overlap": 0.6},
            "fleet": [{"vehicle_id": "geoscan-gemini", "count": 1}],
        }
    )
    work_area = build_work_area(scenario)
    codes = {warning.code for warning in work_area.warnings}
    assert "geo.site_in_nfz" in codes
    assert any(
        warning.severity is Severity.ERROR
        for warning in work_area.warnings
        if warning.code == "geo.site_in_nfz"
    )


def _outer_area_m2(work_area) -> float:
    """Площадь внешнего контура маски без учёта отверстий, м²."""
    from shapely.geometry import Polygon

    return float(Polygon(work_area.area.exterior).area)
