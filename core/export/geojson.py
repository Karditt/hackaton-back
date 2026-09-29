"""Экспорт в GeoJSON (RFC 7946).

Координаты — WGS84 в порядке [долгота, широта, высота], как требует
спецификация. Высота третьим элементом — над поверхностью (AGL); GeoJSON
собственного режима высоты не имеет, поэтому режим продублирован в свойствах
коллекции. Внешние кольца полигонов — против часовой стрелки, отверстия — по
часовой (RFC 7946 §3.1.6). Структура файла описана в docs/export_format.md
(заказчик, №21).

Файл собирается на весь план или на один борт (`board`): тогда в нём только
вылеты, ключевые точки и подобласти этого борта, а слои сценария — целиком.

Невыполнимый план выгружается как отчёт: контекстные слои, места нарушений
(`kind: violation`), предлагаемые места площадок (`kind: recommended_site`),
причины и рекомендации в свойствах коллекции. Маршрутов в нём нет —
частичных планов не бывает (инвариант 10).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from core.errors import GeometryError
from core.export.boards import (
    Board,
    KeyPoint,
    select_boards,
    sortie_board_id,
    sortie_keypoints,
)
from core.export.common import oriented_rings, utc_time
from core.export.schedule import schedule_rows
from core.geo.crs import pick_working_crs
from core.geo.project import Projector
from core.schemas import (
    GeoPolygon,
    Leg,
    MetricLineString,
    MetricPolygon,
    Mission,
    PipelineResult,
    Scenario,
    Sortie,
)

#: Версия формата выгрузки (docs/export_format.md). Меняется при изменении
#: состава признаков или свойств.
FORMAT_VERSION = "geoscan-planner/3"

#: Квадратных метров в квадратном километре — перевод единиц показа.
_M2_PER_KM2 = 1.0e6


def _rings(polygon: GeoPolygon) -> list[list[list[float]]]:
    rings = oriented_rings(
        [(point.lon, point.lat) for point in ring] for ring in [polygon.outer, *polygon.holes]
    )
    return [[list(point) for point in ring] for ring in rings]


def _metric_rings(polygon: MetricPolygon, projector: Projector) -> list[list[list[float]]]:
    rings = oriented_rings(
        projector.coords_to_geo((point.x_m, point.y_m) for point in ring)
        for ring in [polygon.outer, *polygon.holes]
    )
    return [[list(point) for point in ring] for ring in rings]


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

    # Наземные и вертикальные фазы вырождаются в точку на карте: LineString,
    # чьи точки различаются только высотой, большинство просмотрщиков рисует
    # точкой нулевой длины. Их моменты — в ключевых точках (`kind: keypoint`).
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
            "board_id": sortie_board_id(sortie),
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
            "start_time": utc_time(start_time, start_s),
            "end_time": utc_time(start_time, end_s),
        },
    }


def _keypoint_feature(
    sortie: Sortie, point: KeyPoint, projector: Projector, start_time: datetime | None
) -> dict[str, Any]:
    """Ключевая точка вылета → Feature с геометрией Point и высотой."""
    geo = projector.xy_to_geo(point.x_m, point.y_m)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [geo.lon, geo.lat, round(point.alt_m, 1)]},
        "properties": {
            "kind": "keypoint",
            "keypoint": point.kind.value,
            "title": point.title,
            "sortie_id": sortie.id,
            "board_id": sortie_board_id(sortie),
            "vehicle_id": sortie.vehicle_id,
            "vehicle_instance": sortie.vehicle_instance,
            "trip_index": sortie.trip_index,
            "cell_id": point.cell_id,
            "site_id": point.site_id,
            "altitude_m": round(point.alt_m, 1),
            "time_s": round(point.time_s, 1),
            "time": utc_time(start_time, point.time_s),
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
                    "active_from": utc_time(zone.active_from, 0.0),
                    "active_to": utc_time(zone.active_to, 0.0),
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


def _cell_features(
    result: PipelineResult, boards: list[Board], projector: Projector
) -> list[dict[str, Any]]:
    """Подобласти вылетов и зоны манёвра выбранных бортов — «линии и полигоны» (№21)."""
    decomposition = result.decomposition
    coverage = result.coverage
    if decomposition is None or coverage is None:
        return []
    owner: dict[str, Sortie] = {
        cell_id: sortie
        for board in boards
        for sortie in board.sorties
        for cell_id in sortie.cell_ids
    }
    features: list[dict[str, Any]] = []
    for cell in decomposition.cells:
        sortie = owner.get(cell.id)
        if sortie is None:
            continue
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
                    "sortie_id": sortie.id,
                    "board_id": sortie_board_id(sortie),
                    "vehicle_id": sortie.vehicle_id,
                    "area_m2": round(cell.area_m2, 1),
                },
            }
        )
    for item in coverage.cells:
        sortie = owner.get(item.cell_id)
        if sortie is None or sortie.vehicle_id != item.vehicle_id:
            continue
        for zone in item.maneuver_zones:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": _metric_rings(zone, projector)},
                    "properties": {
                        "kind": "maneuver_zone",
                        "cell_id": item.cell_id,
                        "sortie_id": sortie.id,
                        "board_id": sortie_board_id(sortie),
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
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    board: str | None = None,
    names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Собрать FeatureCollection: полётное задание или отчёт о невыполнимости.

    Args:
        result: все стадии прогона (нужны подобласти и зоны манёвра).
        scenario: исходный сценарий (контекстные слои).
        projector: преобразователь рабочей проекции в WGS84.
        board: идентификатор борта (`board_id`) — задание одного борта;
            None — весь план.
        names: название модели по идентификатору — подписи бортов
            «Геоскан Gemini #3»; None — подпись по идентификатору модели.

    Returns:
        Словарь GeoJSON FeatureCollection, готовый к сериализации.

    Raises:
        ExportError: борта с таким идентификатором в плане нет.
    """
    mission = result.mission
    boards = select_boards(mission, board, names)
    features: list[dict[str, Any]] = []
    if mission.is_feasible():
        for item in boards:
            for sortie in item.sorties:
                for leg in sortie.legs:
                    feature = _leg_feature(sortie, leg, projector, scenario.start_time)
                    if feature is not None:
                        features.append(feature)
                features.extend(
                    _keypoint_feature(sortie, point, projector, scenario.start_time)
                    for point in sortie_keypoints(sortie)
                )
        features.extend(_cell_features(result, boards, projector))
    features.extend(_report_features(mission, projector))
    features.extend(_context_features(scenario))

    return {
        "type": "FeatureCollection",
        "features": features,
        # Сторонние члены коллекции: RFC 7946 их допускает, а без них статус,
        # метрики и режим высоты теряются при выгрузке.
        "properties": {
            "format": FORMAT_VERSION,
            "mission_id": mission.id,
            "scenario_id": mission.scenario_id,
            "status": mission.status.value,
            "board_id": board,
            "algorithm": mission.algorithm,
            "mode": mission.mode.value,
            "alpha": mission.alpha,
            "seed": mission.seed,
            "altitude_mode": mission.altitude_mode.value,
            "altitude_reference": "AGL",
            "time_reference": "utc" if scenario.start_time is not None else "relative",
            "start_time": utc_time(scenario.start_time, 0.0),
            "crs": {"epsg_working": mission.crs.epsg, "name": mission.crs.name},
            "metrics": mission.metrics.model_dump(mode="json") if mission.metrics else None,
            "boards": [item.summary() for item in boards],
            "schedule": [row.as_dict() for row in schedule_rows(mission, scenario, board)],
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


def _area_km2(scenario: Scenario) -> float | None:
    """Площадь области съёмки, км²; None — геометрия не переводится в метры.

    Площадь считается в метрической проекции (инвариант 1), как в конвейере.
    Некорректная геометрия здесь не ошибка: этот документ только показывает
    слои, а отвергает их приём сценария и расчёт — с указанием места.
    """
    anchors = [point for polygon in scenario.area.polygons for point in polygon.outer]
    crs, _origin = pick_working_crs(anchors)
    try:
        area = Projector(crs).multipolygon_to_metric(scenario.area, "Область съёмки")
    except GeometryError:
        return None
    return round(area.area / _M2_PER_KM2, 3)


def scenario_to_geojson(scenario: Scenario) -> dict[str, Any]:
    """Слои сценария без расчёта: область, зоны, пространство, препятствия, площадки.

    Нужен интерфейсу, чтобы показать исходные данные на карте до расчёта и
    прятать их по видам (E10). Признаки и поле `kind` — те же, что у слоёв
    сценария в выгрузке плана, поэтому интерфейс рисует оба документа одним
    кодом.

    Args:
        scenario: сценарий с координатами в WGS84.

    Returns:
        Словарь GeoJSON FeatureCollection. В свойствах — число объектов по
        видам и площадь области, км² (None, если геометрия некорректна).
    """
    return {
        "type": "FeatureCollection",
        "features": _context_features(scenario),
        "properties": {
            "format": FORMAT_VERSION,
            "scenario_id": scenario.id,
            "name": scenario.name,
            "altitude_reference": "AGL",
            "area_km2": _area_km2(scenario),
            "counts": {
                "survey_area": len(scenario.area.polygons),
                "no_fly_zone": len(scenario.no_fly_zones),
                "airspace_boundary": int(scenario.airspace_boundary is not None),
                "obstacle": len(scenario.obstacles),
                "site": len(scenario.sites),
            },
        },
    }


def geojson_to_text(document: dict[str, Any]) -> str:
    """Сериализовать FeatureCollection: UTF-8 без экранирования кириллицы."""
    return json.dumps(document, ensure_ascii=False, indent=2)


def write_geojson(
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    path: Path,
    board: str | None = None,
    names: Mapping[str, str] | None = None,
) -> Path:
    """Записать GeoJSON на диск.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь файла.
        board: идентификатор борта; None — весь план.
        names: название модели по идентификатору — подписи бортов.

    Returns:
        Путь записанного файла.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = mission_to_geojson(result, scenario, projector, board, names)
    path.write_text(geojson_to_text(document), encoding="utf-8")
    return path


__all__ = [
    "FORMAT_VERSION",
    "geojson_to_text",
    "mission_to_geojson",
    "scenario_to_geojson",
    "write_geojson",
]
