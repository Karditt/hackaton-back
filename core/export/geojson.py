"""Экспорт в GeoJSON (RFC 7946).

Координаты — WGS84 в порядке [долгота, широта, высота], как требует
спецификация. Высота третьим элементом — над поверхностью (AGL); GeoJSON
собственного режима высоты не имеет, поэтому режим продублирован в свойствах
коллекции. Структура файла описана в docs/export_format.md (заказчик, №21).

Невыполнимый план выгружается как отчёт: контекстные слои, места нарушений
(`kind: violation`), предлагаемые места площадок (`kind: recommended_site`),
причины и рекомендации в свойствах коллекции. Маршрутов в нём нет —
частичных планов не бывает (инвариант 10).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.geo.mask import as_utc
from core.geo.project import Projector
from core.schemas import (
    CoverageSet,
    GeoPolygon,
    Leg,
    MetricLineString,
    MetricPolygon,
    Mission,
    PipelineResult,
    Scenario,
    Sortie,
)


def iso_time(start_time: datetime | None, offset_s: float) -> str | None:
    """Абсолютное время UTC в ISO 8601, если известно начало работ."""
    if start_time is None:
        return None
    return (as_utc(start_time) + timedelta(seconds=offset_s)).isoformat()


def _rings(polygon: GeoPolygon) -> list[list[list[float]]]:
    result = []
    for ring in [polygon.outer, *polygon.holes]:
        coords = [[point.lon, point.lat] for point in ring]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        result.append(coords)
    return result


def _metric_rings(polygon: MetricPolygon, projector: Projector) -> list[list[list[float]]]:
    result = []
    for ring in [polygon.outer, *polygon.holes]:
        coords = [list(item) for item in projector.coords_to_geo((p.x_m, p.y_m) for p in ring)]
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])
        result.append(coords)
    return result


def _line_coords(line: MetricLineString, projector: Projector) -> list[list[float]]:
    return [list(item) for item in projector.coords_to_geo((p.x_m, p.y_m) for p in line.points)]


def _leg_feature(
    sortie: Sortie, leg: Leg, projector: Projector, start_time: datetime | None
) -> dict[str, Any] | None:
    """Участок полёта → Feature с геометрией LineString."""
    coords: list[list[float]] = []
    for waypoint in leg.waypoints:
        geo = projector.xy_to_geo(waypoint.x_m, waypoint.y_m)
        coords.append([geo.lon, geo.lat, round(waypoint.alt_m, 1)])

    # Наземные и вертикальные фазы вырождаются в точку: LineString из двух
    # одинаковых координат невалиден по RFC 7946 и ломает часть просмотрщиков.
    unique = {(round(item[0], 9), round(item[1], 9)) for item in coords}
    if len(unique) < 2:
        return None

    start_s = leg.waypoints[0].time_s
    end_s = leg.waypoints[-1].time_s
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "kind": "leg",
            "sortie_id": sortie.id,
            "vehicle_id": sortie.vehicle_id,
            "vehicle_instance": sortie.vehicle_instance,
            "trip_index": sortie.trip_index,
            "takeoff_site_id": sortie.takeoff_site_id,
            "landing_site_id": sortie.landing_site_id,
            "phase": leg.phase.value,
            "cell_id": leg.cell_id,
            "strip_id": leg.strip_id,
            "length_m": round(leg.length_m, 1),
            "duration_s": round(leg.duration_s, 1),
            "ground_speed_mps": round(leg.ground_speed_mps, 2),
            "altitude_m": round(leg.waypoints[0].alt_m, 1),
            "start_s": round(start_s, 1),
            "end_s": round(end_s, 1),
            "start_time": iso_time(start_time, start_s),
            "end_time": iso_time(start_time, end_s),
        },
    }


def _context_features(scenario: Scenario) -> list[dict[str, Any]]:
    """Слои сценария: область, зоны, пространство, препятствия, площадки."""
    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [_rings(polygon) for polygon in scenario.area.polygons],
            },
            "properties": {"kind": "survey_area", "name": scenario.name},
        }
    ]
    for zone in scenario.no_fly_zones:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": _rings(zone.polygon)},
                "properties": {
                    "kind": "no_fly_zone",
                    "id": zone.id,
                    "name": zone.name,
                    "min_alt_m": zone.min_alt_m,
                    "max_alt_m": zone.max_alt_m,
                    "active_from": zone.active_from.isoformat() if zone.active_from else None,
                    "active_to": zone.active_to.isoformat() if zone.active_to else None,
                },
            }
        )
    if scenario.airspace_boundary is not None:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": _rings(scenario.airspace_boundary)},
                "properties": {"kind": "airspace_boundary"},
            }
        )
    for obstacle in scenario.obstacles:
        geometry = (
            {"type": "Polygon", "coordinates": _rings(obstacle.polygon)}
            if obstacle.polygon is not None
            else {
                "type": "Point",
                "coordinates": [obstacle.position.lon, obstacle.position.lat],  # type: ignore[union-attr]
            }
        )
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "kind": "obstacle",
                    "id": obstacle.id,
                    "name": obstacle.name,
                    "height_m": obstacle.height_m,
                },
            }
        )
    for site in scenario.sites:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [site.position.lon, site.position.lat],
                },
                "properties": {
                    "kind": "site",
                    "site_id": site.id,
                    "name": site.name,
                    "site_kind": site.kind.value,
                },
            }
        )
    return features


def _cell_features(result: PipelineResult, projector: Projector) -> list[dict[str, Any]]:
    """Подобласти вылетов и зоны манёвра — «линии и полигоны» (№21)."""
    decomposition = result.decomposition
    coverage: CoverageSet | None = result.coverage
    if decomposition is None or coverage is None:
        return []
    owner = {
        cell_id: (assignment.sortie_id, assignment.vehicle_id)
        for assignment in (result.allocation.assignments if result.allocation else [])
        for cell_id in assignment.cell_ids
    }
    features: list[dict[str, Any]] = []
    for cell in decomposition.cells:
        sortie_id, vehicle_id = owner.get(cell.id, (None, None))
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": _metric_rings(cell.polygon, projector),
                },
                "properties": {
                    "kind": "survey_cell",
                    "cell_id": cell.id,
                    "sortie_id": sortie_id,
                    "vehicle_id": vehicle_id,
                    "area_m2": round(cell.area_m2, 1),
                },
            }
        )
    for item in coverage.cells:
        for zone in item.maneuver_zones:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": _metric_rings(zone, projector)},
                    "properties": {
                        "kind": "maneuver_zone",
                        "cell_id": item.cell_id,
                        "vehicle_id": item.vehicle_id,
                    },
                }
            )
    return features


def _report_features(mission: Mission, projector: Projector) -> list[dict[str, Any]]:
    """Места нарушений и предлагаемые места площадок."""
    features: list[dict[str, Any]] = []
    for reason in mission.reasons:
        if reason.geometry is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": _line_coords(reason.geometry, projector),
                },
                "properties": {"kind": "violation", "code": reason.code, "message": reason.message},
            }
        )
    for item in mission.recommendations:
        if item.position is None:
            continue
        geo = projector.to_geo(item.position)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [geo.lon, geo.lat]},
                "properties": {
                    "kind": "recommended_site",
                    "recommendation": item.kind.value,
                    "message": item.message,
                },
            }
        )
    return features


def mission_to_geojson(
    result: PipelineResult, scenario: Scenario, projector: Projector
) -> dict[str, Any]:
    """Собрать FeatureCollection: полётное задание или отчёт о невыполнимости.

    Args:
        result: все стадии прогона (нужны подобласти и зоны манёвра).
        scenario: исходный сценарий (контекстные слои).
        projector: преобразователь рабочей проекции в WGS84.

    Returns:
        Словарь GeoJSON FeatureCollection, готовый к сериализации.
    """
    mission = result.mission
    features: list[dict[str, Any]] = []
    if mission.is_feasible():
        for sortie in mission.sorties:
            for leg in sortie.legs:
                feature = _leg_feature(sortie, leg, projector, scenario.start_time)
                if feature is not None:
                    features.append(feature)
        features.extend(_cell_features(result, projector))
    features.extend(_report_features(mission, projector))
    features.extend(_context_features(scenario))

    return {
        "type": "FeatureCollection",
        "features": features,
        # Сторонние члены коллекции: RFC 7946 их допускает, а без них статус,
        # метрики и режим высоты теряются при выгрузке.
        "properties": {
            "format": "geoscan-planner/2",
            "mission_id": mission.id,
            "scenario_id": mission.scenario_id,
            "status": mission.status.value,
            "algorithm": mission.algorithm,
            "mode": mission.mode.value,
            "alpha": mission.alpha,
            "seed": mission.seed,
            "altitude_mode": mission.altitude_mode.value,
            "altitude_reference": "AGL",
            "start_time": scenario.start_time.isoformat() if scenario.start_time else None,
            "crs": {"epsg_working": mission.crs.epsg, "name": mission.crs.name},
            "metrics": mission.metrics.model_dump(mode="json") if mission.metrics else None,
            "schedule": (
                [entry.model_dump(mode="json") for entry in mission.schedule.entries]
                if mission.schedule
                else []
            ),
            "reasons": [
                reason.model_dump(mode="json", exclude={"geometry"}) for reason in mission.reasons
            ],
            "recommendations": [
                item.model_dump(mode="json", exclude={"position"})
                for item in mission.recommendations
            ],
            "stub_stages": [stage.value for stage in mission.stub_stages],
            "warnings": [
                warning.model_dump(mode="json", exclude={"geometry"})
                for warning in mission.warnings
            ],
        },
    }


def write_geojson(
    result: PipelineResult, scenario: Scenario, projector: Projector, path: Path
) -> Path:
    """Записать GeoJSON на диск.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь файла.

    Returns:
        Путь записанного файла.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = mission_to_geojson(result, scenario, projector)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = ["iso_time", "mission_to_geojson", "write_geojson"]
