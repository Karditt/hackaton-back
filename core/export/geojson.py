"""Экспорт плана в GeoJSON (RFC 7946).

Координаты — WGS84 в порядке [долгота, широта, высота], как требует
спецификация. Высота третьим элементом означает то, что записано в
`Mission.altitude_mode`; GeoJSON собственного режима высоты не имеет, поэтому
режим продублирован в свойствах коллекции — иначе цифра не интерпретируема.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.geo.project import Projector
from core.schemas import Leg, Mission, Scenario, Sortie


def _leg_feature(sortie: Sortie, leg: Leg, projector: Projector) -> dict[str, Any] | None:
    """Участок полёта → Feature с геометрией LineString."""
    coords: list[list[float]] = []
    for waypoint in leg.waypoints:
        geo = projector.xy_to_geo(waypoint.x_m, waypoint.y_m, waypoint.alt_m)
        coords.append([geo.lon, geo.lat, waypoint.alt_m])

    # Наземные и вертикальные фазы вырождаются в точку: LineString из двух
    # одинаковых координат невалиден по RFC 7946 и ломает часть просмотрщиков.
    unique = {(round(item[0], 9), round(item[1], 9)) for item in coords}
    if len(unique) < 2:
        return None

    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "sortie_id": sortie.id,
            "vehicle_id": sortie.vehicle_id,
            "vehicle_instance": sortie.vehicle_instance,
            "site_id": sortie.site_id,
            "trip_index": sortie.trip_index,
            "phase": leg.phase.value,
            "strip_id": leg.strip_id,
            "length_m": round(leg.length_m, 1),
            "duration_s": round(leg.duration_s, 1),
            "ground_speed_mps": round(leg.ground_speed_mps, 2),
            "energy_wh": None if leg.energy_wh is None else round(leg.energy_wh, 1),
            "altitude_m": round(leg.waypoints[0].alt_m, 1),
        },
    }


def _site_features(scenario: Scenario) -> list[dict[str, Any]]:
    """Площадки взлёта-посадки → точечные Feature."""
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [site.position.lon, site.position.lat]},
            "properties": {"site_id": site.id, "name": site.name, "kind": site.kind.value},
        }
        for site in scenario.sites
    ]


def _area_features(scenario: Scenario) -> list[dict[str, Any]]:
    """Область съёмки и запретные зоны → полигональные Feature."""

    def rings(polygon) -> list[list[list[float]]]:
        result = []
        for ring in [polygon.outer, *polygon.holes]:
            coords = [[point.lon, point.lat] for point in ring]
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            result.append(coords)
        return result

    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": rings(scenario.area)},
            "properties": {"kind": "survey_area", "name": scenario.name},
        }
    ]
    features.extend(
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": rings(zone.polygon)},
            "properties": {"kind": "no_fly_zone", "id": zone.id, "name": zone.name},
        }
        for zone in scenario.no_fly_zones
    )
    if scenario.airspace_boundary is not None:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": rings(scenario.airspace_boundary),
                },
                "properties": {"kind": "airspace_boundary"},
            }
        )
    return features


def mission_to_geojson(
    mission: Mission, scenario: Scenario, projector: Projector
) -> dict[str, Any]:
    """Собрать FeatureCollection полётного задания.

    Args:
        mission: готовый план.
        scenario: исходный сценарий (нужен для контекстных слоёв).
        projector: преобразователь рабочей проекции в WGS84.

    Returns:
        Словарь GeoJSON FeatureCollection, готовый к сериализации.
    """
    features: list[dict[str, Any]] = []
    for sortie in mission.sorties:
        for leg in sortie.legs:
            feature = _leg_feature(sortie, leg, projector)
            if feature is not None:
                features.append(feature)
    features.extend(_site_features(scenario))
    features.extend(_area_features(scenario))

    return {
        "type": "FeatureCollection",
        "features": features,
        # Сторонние члены коллекции: RFC 7946 их допускает, а без них метрики
        # и режим высоты теряются при выгрузке.
        "properties": {
            "mission_id": mission.id,
            "scenario_id": mission.scenario_id,
            "generator": "geoscan-planner",
            "algorithm": mission.algorithm,
            "alpha": mission.alpha,
            "seed": mission.seed,
            "altitude_mode": mission.altitude_mode.value,
            "crs": {"epsg_working": mission.crs.epsg, "name": mission.crs.name},
            "metrics": mission.metrics.model_dump(mode="json"),
            "stub_stages": [stage.value for stage in mission.stub_stages],
            "warnings": [warning.model_dump(mode="json") for warning in mission.warnings],
        },
    }


def write_geojson(mission: Mission, scenario: Scenario, projector: Projector, path: Path) -> Path:
    """Записать GeoJSON на диск.

    Args:
        mission: готовый план.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь файла.

    Returns:
        Путь записанного файла.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = mission_to_geojson(mission, scenario, projector)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = ["mission_to_geojson", "write_geojson"]
