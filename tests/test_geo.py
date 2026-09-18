"""Инвариант 1 и приём геометрии: метры, валидация, зоны, препятствия, маска."""

from __future__ import annotations

import math

import pytest

from core.errors import GeometryError, ScenarioError
from core.geo.crs import pick_working_crs, utm_epsg, utm_zone
from core.geo.mask import build_survey_mask, build_work_area
from core.geo.project import Projector
from core.schemas import CrsInfo, GeoPoint, GeoPolygon, Scenario, Severity
from tests.conftest import small_scenario

_ZONE = [[30.305, 59.903], [30.312, 59.903], [30.312, 59.907], [30.305, 59.907]]


def test_utm_zone_by_longitude() -> None:
    """Зона UTM считается по долготе: Санкт-Петербург — 36-я."""
    assert utm_zone(30.3) == 36
    assert utm_zone(-179.9) == 1
    assert utm_zone(179.9) == 60


def test_utm_epsg_depends_on_hemisphere() -> None:
    """Северное полушарие — 326NN, южное — 327NN."""
    assert utm_epsg(30.3, 59.9) == 32636
    assert utm_epsg(30.3, -33.9) == 32736


def test_projection_roundtrip_is_accurate() -> None:
    """Прямое и обратное преобразование возвращают исходную точку."""
    crs, _ = pick_working_crs([GeoPoint(lon=30.3, lat=59.9)])
    projector = Projector(crs)
    source = GeoPoint(lon=30.31234, lat=59.91234)
    restored = projector.to_geo(projector.to_metric(source))
    assert restored.lon == pytest.approx(source.lon, abs=1e-9)
    assert restored.lat == pytest.approx(source.lat, abs=1e-9)


def test_distances_are_metric_not_degrees() -> None:
    """Ровно та ошибка, ради которой существует инвариант 1: градус широты ≈ 111 км."""
    crs, _ = pick_working_crs([GeoPoint(lon=30.3, lat=59.9)])
    projector = Projector(crs)
    south = projector.to_metric(GeoPoint(lon=30.3, lat=59.9))
    north = projector.to_metric(GeoPoint(lon=30.3, lat=60.9))
    assert 110_000 < math.hypot(north.x_m - south.x_m, north.y_m - south.y_m) < 112_000


def test_geographic_crs_is_rejected() -> None:
    """Попытка работать в EPSG:4326 внутри ядра должна падать."""
    with pytest.raises(GeometryError):
        Projector(CrsInfo(epsg=4326, name="WGS84"))


def test_degenerate_polygon_is_rejected() -> None:
    """Полигон нулевой площади — ошибка геометрии, а не пустой план."""
    crs, _ = pick_working_crs([GeoPoint(lon=30.3, lat=59.9)])
    flat = GeoPolygon.model_validate([[[30.3, 59.9], [30.3, 59.9], [30.3, 59.9]]])
    with pytest.raises(GeometryError):
        Projector(crs).polygon_to_metric(flat)


def test_self_intersection_is_rejected_with_its_location() -> None:
    """Заказчик, №9, №19: самопересечение — ошибка с указанием места, а не молчаливая починка."""
    bow_tie = [[[30.30, 59.90], [30.32, 59.91], [30.32, 59.90], [30.30, 59.91]]]
    with pytest.raises(GeometryError) as error:
        build_work_area(small_scenario(area=bow_tie))
    message = str(error.value)
    assert "самопересечение" in message
    assert "30.31" in message


def test_area_above_limit_is_rejected() -> None:
    """Область в тысячи км² — почти наверняка ошибка ввода (№19)."""
    huge = [[[29.0, 59.0], [31.0, 59.0], [31.0, 60.0], [29.0, 60.0]]]
    with pytest.raises(ScenarioError):
        build_work_area(small_scenario(area=huge))


def test_multipolygon_area_is_kept_whole(scenario: Scenario) -> None:
    """Мультиполигон переводится в метры целиком: ни одна часть не теряется."""
    work_area = build_work_area(scenario)
    parts = list(getattr(work_area.area, "geoms", [work_area.area]))
    assert len(parts) == len(scenario.area.polygons)
    assert work_area.area_m2 > 0


def test_permanent_zone_is_cut_from_survey_mask() -> None:
    """Постоянная зона на высоте съёмки вычитается из маски вместе с буфером."""
    scenario = small_scenario(no_fly_zones=[{"id": "z", "polygon": [_ZONE]}])
    work_area = build_work_area(scenario)
    mask, _ = build_survey_mask(work_area, 200.0, 250.0)
    assert mask.area < work_area.area_m2
    assert not mask.intersects(work_area.zones[0].inner)


def test_low_zone_does_not_block_survey_above_it() -> None:
    """Зона до 120 м не мешает съёмке на 200 м: высоты зоны учитываются (№40)."""
    scenario = small_scenario(no_fly_zones=[{"id": "z", "polygon": [_ZONE], "max_alt_m": 120}])
    work_area = build_work_area(scenario)
    mask, _ = build_survey_mask(work_area, 200.0, 250.0)
    assert mask.area == pytest.approx(work_area.area_m2)


def test_temporary_zone_is_not_cut_when_start_time_is_known() -> None:
    """Под временной зоной снимать надо — вне окна; это забота расписания."""
    zone = {
        "id": "t",
        "polygon": [_ZONE],
        "active_from": "2026-09-20T07:00:00Z",
        "active_to": "2026-09-20T09:00:00Z",
    }
    timed = build_work_area(small_scenario(no_fly_zones=[zone], start_time="2026-09-20T06:00:00Z"))
    mask, _ = build_survey_mask(timed, 200.0, 250.0)
    assert mask.area == pytest.approx(timed.area_m2)
    assert timed.zones[0].active_from_s == pytest.approx(3600.0)
    assert timed.zones[0].active_to_s == pytest.approx(3 * 3600.0)


def test_temporary_zone_without_start_time_is_conservative() -> None:
    """Без времени начала работ окно не с чем сопоставить: зона действует всегда."""
    zone = {
        "id": "t",
        "polygon": [_ZONE],
        "active_from": "2026-09-20T07:00:00Z",
        "active_to": "2026-09-20T09:00:00Z",
    }
    work_area = build_work_area(small_scenario(no_fly_zones=[zone]))
    assert work_area.zones[0].permanent
    assert any(w.code == "geo.temporal_zone_without_start" for w in work_area.warnings)


def test_obstacles_block_only_below_their_safe_altitude() -> None:
    """Препятствие мешает, только если высота ниже его вершины плюс запас."""
    scenario = small_scenario(
        obstacles=[
            {"id": "mast", "position": [30.31, 59.905], "height_m": 250},
            {"id": "house", "position": [30.305, 59.902], "height_m": 30},
        ]
    )
    work_area = build_work_area(scenario)
    mask, _ = build_survey_mask(work_area, 200.0, 250.0)
    assert mask.area < work_area.area_m2
    mast, house = work_area.obstacles
    assert mast.blocks_altitude(250.0)
    assert not house.blocks_altitude(200.0)


def test_site_inside_zone_is_reported() -> None:
    """Площадка внутри запретной зоны — предупреждение уровня ОШИБКА."""
    scenario = small_scenario(
        no_fly_zones=[{"id": "z", "polygon": [_ZONE]}],
        sites=[{"id": "base", "position": [30.308, 59.905]}],
    )
    warnings = build_work_area(scenario).warnings
    assert any(w.code == "geo.site_in_nfz" and w.severity is Severity.ERROR for w in warnings)
