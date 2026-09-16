"""Контракт данных: что должно приниматься и что обязано падать."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.schemas import (
    GeoPoint,
    GeoPolygon,
    MetricPoint,
    Scenario,
    Strip,
    Wind,
)


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


def test_geo_polygon_accepts_geojson_rings() -> None:
    """Полигон принимает координаты в форме GeoJSON Polygon: контур, потом дырки."""
    polygon = GeoPolygon.model_validate(
        [
            [[30.0, 59.0], [30.1, 59.0], [30.1, 59.1], [30.0, 59.1]],
            [[30.02, 59.02], [30.04, 59.02], [30.04, 59.04], [30.02, 59.04]],
        ]
    )
    assert len(polygon.outer) == 4
    assert len(polygon.holes) == 1


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


def test_scenario_requires_site_and_fleet(simple_scenario: Scenario) -> None:
    """Сценарий без площадок или без флота не является сценарием."""
    payload = simple_scenario.model_dump(mode="json")
    payload["sites"] = []
    with pytest.raises(ValidationError):
        Scenario.model_validate(payload)

    payload = simple_scenario.model_dump(mode="json")
    payload["fleet"] = []
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


def test_strip_endpoints() -> None:
    """Галс отдаёт свои концы — на них опирается сборка вылета."""
    strip = Strip.model_validate(
        {
            "id": "s0",
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
