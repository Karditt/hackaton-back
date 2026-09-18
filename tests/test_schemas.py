"""Контракт данных: что должно приниматься и что обязано падать."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.schemas import (
    GeoMultiPolygon,
    GeoPoint,
    GeoPolygon,
    MetricPoint,
    NoFlyZone,
    Obstacle,
    Scenario,
    Strip,
    Wind,
)
from tests.conftest import small_scenario

_SQUARE = [[30.0, 59.0], [30.1, 59.0], [30.1, 59.1], [30.0, 59.1]]


def test_geo_point_accepts_compact_lon_lat() -> None:
    """Компактная запись [lon, lat] — как в GeoJSON: сначала долгота."""
    point = GeoPoint.model_validate([30.3, 59.9])
    assert point.lon == pytest.approx(30.3)
    assert point.lat == pytest.approx(59.9)


def test_geo_point_rejects_swapped_lat_lon() -> None:
    """Перепутанные местами широта и долгота должны падать, а не молча считаться."""
    with pytest.raises(ValidationError):
        GeoPoint.model_validate([59.9, 130.3])


def test_metric_point_is_not_interchangeable_with_geo_point() -> None:
    """Инвариант 1: метры и градусы — разные типы, подмена невозможна."""
    with pytest.raises(ValidationError):
        MetricPoint.model_validate({"lon": 30.3, "lat": 59.9})
    with pytest.raises(ValidationError):
        GeoPoint.model_validate({"x_m": 100.0, "y_m": 200.0})


def test_geo_polygon_accepts_geojson_rings_and_geometry() -> None:
    """Полигон — кольца GeoJSON Polygon или геометрия GeoJSON целиком."""
    rings = [_SQUARE, [[30.02, 59.02], [30.04, 59.02], [30.04, 59.04], [30.02, 59.04]]]
    polygon = GeoPolygon.model_validate(rings)
    assert len(polygon.outer) == 4
    assert len(polygon.holes) == 1
    assert GeoPolygon.model_validate({"type": "Polygon", "coordinates": rings}) == polygon


def test_area_accepts_polygon_and_multipolygon() -> None:
    """Заказчик, №9: область — полигон или мультиполигон с отверстиями."""
    single = GeoMultiPolygon.model_validate([_SQUARE])
    assert len(single.polygons) == 1
    shifted = [[lon + 0.2, lat] for lon, lat in _SQUARE]
    multi = GeoMultiPolygon.model_validate([[_SQUARE], [shifted]])
    assert len(multi.polygons) == 2
    geometry = GeoMultiPolygon.model_validate(
        {"type": "MultiPolygon", "coordinates": [[_SQUARE], [shifted]]}
    )
    assert geometry == multi


def test_area_rejects_other_geometry_types() -> None:
    """Точка вместо области — ошибка, а не пустой план."""
    with pytest.raises(ValidationError):
        GeoMultiPolygon.model_validate({"type": "Point", "coordinates": [30.0, 59.0]})


def test_obstacle_needs_exactly_one_geometry() -> None:
    """Препятствие — точка или полигон, но не оба и не ничего."""
    Obstacle.model_validate({"id": "mast", "position": [30.0, 59.0], "height_m": 100})
    Obstacle.model_validate({"id": "house", "polygon": [_SQUARE], "height_m": 20})
    with pytest.raises(ValidationError):
        Obstacle.model_validate({"id": "none", "height_m": 10})
    with pytest.raises(ValidationError):
        Obstacle.model_validate(
            {"id": "both", "position": [30.0, 59.0], "polygon": [_SQUARE], "height_m": 10}
        )


def test_no_fly_zone_ranges_must_be_ordered() -> None:
    """Заказчик, №40: у зоны высоты и окно действия — пустой диапазон отвергается."""
    base = {"id": "z", "polygon": [_SQUARE]}
    NoFlyZone.model_validate({**base, "min_alt_m": 0, "max_alt_m": 120})
    with pytest.raises(ValidationError):
        NoFlyZone.model_validate({**base, "min_alt_m": 200, "max_alt_m": 100})
    with pytest.raises(ValidationError):
        NoFlyZone.model_validate(
            {**base, "active_from": "2026-09-20T09:00:00Z", "active_to": "2026-09-20T07:00:00Z"}
        )


def test_unknown_field_is_rejected() -> None:
    """Опечатка в имени поля сценария не должна игнорироваться."""
    with pytest.raises(ValidationError):
        Wind.model_validate({"speed_mps": 5.0, "direction_degrees": 90.0})


def test_wind_direction_bounds() -> None:
    """Направление ветра — 0..360 градусов."""
    Wind(speed_mps=5.0, direction_deg=0.0)
    Wind(speed_mps=5.0, direction_deg=360.0)
    with pytest.raises(ValidationError):
        Wind(speed_mps=5.0, direction_deg=361.0)


def test_scenario_requires_site_fleet_and_corridor(simple_scenario: Scenario) -> None:
    """Сценарий без площадок, флота или коридора высот (№10) не является сценарием."""
    for field in ("sites", "fleet"):
        payload = simple_scenario.model_dump(mode="json")
        payload[field] = []
        with pytest.raises(ValidationError):
            Scenario.model_validate(payload)
    payload = simple_scenario.model_dump(mode="json")
    del payload["altitude_corridor"]
    with pytest.raises(ValidationError):
        Scenario.model_validate(payload)


def test_scenario_rejects_fleet_pinned_to_unknown_site(simple_scenario: Scenario) -> None:
    """Приписка борта к несуществующей площадке — ошибка сценария."""
    payload = simple_scenario.model_dump(mode="json")
    payload["fleet"][0]["site_id"] = "no-such-site"
    with pytest.raises(ValidationError):
        Scenario.model_validate(payload)


def test_scenario_rejects_duplicate_site_ids(simple_scenario: Scenario) -> None:
    """Две площадки с одним идентификатором сделали бы назначение неоднозначным."""
    payload = simple_scenario.model_dump(mode="json")
    payload["sites"] = [payload["sites"][0], payload["sites"][0]]
    with pytest.raises(ValidationError):
        Scenario.model_validate(payload)


def test_reserve_sites_alone_are_not_enough() -> None:
    """Заказчик, №41: резервные площадки для основной работы не используются."""
    with pytest.raises(ValidationError):
        small_scenario(sites=[{"id": "r", "position": [30.31, 59.898], "kind": "reserve"}])


def test_fleet_size_is_limited() -> None:
    """Заказчик, №19, №43: число бортов проверяется — «не 1 000 000»."""
    small_scenario(fleet=[{"vehicle_id": "geoscan-gemini", "count": 15}])
    with pytest.raises(ValidationError):
        small_scenario(fleet=[{"vehicle_id": "geoscan-gemini", "count": 16}])


def test_strip_endpoints() -> None:
    """Галс отдаёт свои концы — на них опирается сборка вылета."""
    strip = Strip.model_validate(
        {
            "id": "s0",
            "cell_id": "c0",
            "vehicle_id": "v0",
            "line": [[0.0, 0.0], [100.0, 0.0]],
            "length_m": 100.0,
            "altitude_agl_m": 120.0,
            "speed_mps": 10.0,
            "azimuth_deg": 90.0,
        }
    )
    assert strip.start().x_m == pytest.approx(0.0)
    assert strip.end().x_m == pytest.approx(100.0)


def test_scenario_roundtrip_is_lossless(scenario: Scenario) -> None:
    """Сценарий переживает сериализацию без потерь — это условие работы API."""
    restored = Scenario.model_validate_json(scenario.model_dump_json())
    assert restored == scenario
