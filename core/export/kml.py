"""Экспорт в KML (OGC 2.2).

Режим высоты выставляется ЯВНО. `clampToGround`, `relativeToGround` и
`absolute` дают три разных смысла одной и той же цифре, и умолчание KML —
`clampToGround`, то есть «положить на землю». Высоты проекта — над
поверхностью (AGL, заказчик, №10), что соответствует `relativeToGround`.

Структура (docs/export_format.md, заказчик, №21): папка на борт → папка на
вылет → участки по фазам с метками времени (`TimeSpan`, если задано начало
работ) и полигоны подобластей и зон манёвра; отдельно — слои сценария.
Невыполнимый план выгружается отчётом: причины с местами нарушений и
рекомендации, без маршрутов (инвариант 10).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from core.export.geojson import iso_time
from core.geo.project import Projector
from core.schemas import (
    FlightPhase,
    GeoPolygon,
    MetricPolygon,
    PipelineResult,
    Scenario,
    Sortie,
)

_KML_NS = "http://www.opengis.net/kml/2.2"

#: Цвета фаз в формате KML aabbggrr (альфа, синий, зелёный, красный).
_PHASE_COLORS: dict[FlightPhase, str] = {
    FlightPhase.TAKEOFF: "ff00ff00",
    FlightPhase.CLIMB: "ff20c0ff",
    FlightPhase.TRANSIT: "ff909090",
    FlightPhase.STRIP: "ffff3030",
    FlightPhase.TURN: "ff00a5ff",
    FlightPhase.HOLD: "ffff00ff",
    FlightPhase.DESCENT: "ff20c0ff",
    FlightPhase.LANDING: "ff0000ff",
}


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    """Создать дочерний элемент с текстом."""
    element = ET.SubElement(parent, tag)
    if text is not None:
        element.text = text
    return element


def _styles(document: ET.Element) -> None:
    """Стили фаз, подобластей, зон манёвра, нарушений."""
    for phase, color in _PHASE_COLORS.items():
        style = ET.SubElement(document, "Style", {"id": f"phase-{phase.value}"})
        line = _sub(style, "LineStyle")
        _sub(line, "color", color)
        _sub(line, "width", "3" if phase is FlightPhase.STRIP else "2")
    for style_id, line_color, fill_color in (
        ("cell", "ffcc6600", "22cc6600"),
        ("maneuver", "ff00a5ff", "2200a5ff"),
        ("nfz", "ff0000ff", "440000ff"),
        ("area", "ffff0000", "11ff0000"),
        ("obstacle", "ff000080", "88000080"),
    ):
        style = ET.SubElement(document, "Style", {"id": style_id})
        _sub(_sub(style, "LineStyle"), "color", line_color)
        _sub(_sub(style, "PolyStyle"), "color", fill_color)
    violation = ET.SubElement(document, "Style", {"id": "violation"})
    line = _sub(violation, "LineStyle")
    _sub(line, "color", "ff0000ff")
    _sub(line, "width", "5")


def _extended(parent: ET.Element, values: dict[str, object]) -> None:
    """Атрибуты в ExtendedData — машиночитаемо, как в GeoJSON."""
    data = _sub(parent, "ExtendedData")
    for name, value in values.items():
        if value is None:
            continue
        item = ET.SubElement(data, "Data", {"name": name})
        _sub(item, "value", str(value))


def _polygon(parent: ET.Element, rings: list[list[tuple[float, float]]]) -> None:
    """Полигон KML, прижатый к земле."""
    polygon = _sub(parent, "Polygon")
    _sub(polygon, "altitudeMode", "clampToGround")
    for index, ring in enumerate(rings):
        boundary = _sub(polygon, "outerBoundaryIs" if index == 0 else "innerBoundaryIs")
        coords = list(ring)
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])
        _sub(
            _sub(boundary, "LinearRing"),
            "coordinates",
            " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in coords),
        )


def _geo_rings(polygon: GeoPolygon) -> list[list[tuple[float, float]]]:
    return [[(point.lon, point.lat) for point in ring] for ring in [polygon.outer, *polygon.holes]]


def _metric_rings(polygon: MetricPolygon, projector: Projector) -> list[list[tuple[float, float]]]:
    return [
        projector.coords_to_geo((point.x_m, point.y_m) for point in ring)
        for ring in [polygon.outer, *polygon.holes]
    ]


def _sortie_folder(
    parent: ET.Element,
    sortie: Sortie,
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    altitude_mode: str,
) -> None:
    """Папка одного вылета: участки по фазам, подобласти, зоны манёвра."""
    folder = _sub(parent, "Folder")
    _sub(folder, "name", f"Вылет {sortie.trip_index + 1} · {sortie.id}")
    start_iso = iso_time(scenario.start_time, sortie.start_time_s)
    _sub(
        folder,
        "description",
        f"Взлёт {sortie.takeoff_site_id}, посадка {sortie.landing_site_id}. "
        f"Старт через {sortie.start_time_s / 60:.1f} мин от начала работ"
        + (f" ({start_iso})" if start_iso else "")
        + f", длительность {sortie.duration_s / 60:.1f} мин, дистанция "
        f"{sortie.distance_m / 1000:.1f} км, съёмка на {sortie.survey_altitude_m:.0f} м, "
        f"перелёт на {sortie.transit_altitude_m:.0f} м AGL",
    )

    for index, leg in enumerate(sortie.legs):
        coords: list[str] = []
        for waypoint in leg.waypoints:
            geo = projector.xy_to_geo(waypoint.x_m, waypoint.y_m)
            coords.append(f"{geo.lon:.8f},{geo.lat:.8f},{waypoint.alt_m:.1f}")
        if len({item.rsplit(",", 1)[0] for item in coords}) < 2:
            continue
        placemark = _sub(folder, "Placemark")
        _sub(placemark, "name", f"{index + 1:02d} · {leg.phase.value}")
        start_s, end_s = leg.waypoints[0].time_s, leg.waypoints[-1].time_s
        begin, end = iso_time(scenario.start_time, start_s), iso_time(scenario.start_time, end_s)
        if begin and end:
            span = _sub(placemark, "TimeSpan")
            _sub(span, "begin", begin)
            _sub(span, "end", end)
        _sub(placemark, "styleUrl", f"#phase-{leg.phase.value}")
        _extended(
            placemark,
            {
                "sortie_id": sortie.id,
                "vehicle_id": sortie.vehicle_id,
                "phase": leg.phase.value,
                "cell_id": leg.cell_id,
                "strip_id": leg.strip_id,
                "length_m": round(leg.length_m, 1),
                "duration_s": round(leg.duration_s, 1),
                "ground_speed_mps": round(leg.ground_speed_mps, 2),
                "altitude_agl_m": round(leg.waypoints[0].alt_m, 1),
                "start_s": round(start_s, 1),
                "end_s": round(end_s, 1),
            },
        )
        line = _sub(placemark, "LineString")
        _sub(line, "extrude", "0")
        _sub(line, "tessellate", "0")
        _sub(line, "altitudeMode", altitude_mode)
        _sub(line, "coordinates", " ".join(coords))

    decomposition = result.decomposition
    coverage = result.coverage
    if decomposition is None or coverage is None:
        return
    for cell_id in sortie.cell_ids:
        cell = decomposition.cell(cell_id)
        if cell is not None:
            placemark = _sub(folder, "Placemark")
            _sub(placemark, "name", f"Подобласть {cell_id}")
            _sub(placemark, "styleUrl", "#cell")
            _extended(placemark, {"kind": "survey_cell", "cell_id": cell_id})
            _polygon(placemark, _metric_rings(cell.polygon, projector))
        for item in coverage.cells:
            if item.cell_id != cell_id or item.vehicle_id != sortie.vehicle_id:
                continue
            for zone in item.maneuver_zones:
                placemark = _sub(folder, "Placemark")
                _sub(placemark, "name", f"Зона манёвра {cell_id}")
                _sub(placemark, "styleUrl", "#maneuver")
                _extended(placemark, {"kind": "maneuver_zone", "cell_id": cell_id})
                _polygon(placemark, _metric_rings(zone, projector))


def _context_folder(document: ET.Element, scenario: Scenario) -> None:
    """Слои сценария: область, зоны, препятствия, площадки."""
    folder = _sub(document, "Folder")
    _sub(folder, "name", "Сценарий")
    for index, polygon in enumerate(scenario.area.polygons):
        placemark = _sub(folder, "Placemark")
        _sub(placemark, "name", f"Область съёмки, часть {index + 1}")
        _sub(placemark, "styleUrl", "#area")
        _polygon(placemark, _geo_rings(polygon))
    for zone in scenario.no_fly_zones:
        placemark = _sub(folder, "Placemark")
        _sub(placemark, "name", f"Запретная зона {zone.name or zone.id}")
        if zone.active_from or zone.active_to:
            span = _sub(placemark, "TimeSpan")
            if zone.active_from:
                _sub(span, "begin", zone.active_from.isoformat())
            if zone.active_to:
                _sub(span, "end", zone.active_to.isoformat())
        _sub(placemark, "styleUrl", "#nfz")
        _extended(
            placemark,
            {"kind": "no_fly_zone", "min_alt_m": zone.min_alt_m, "max_alt_m": zone.max_alt_m},
        )
        _polygon(placemark, _geo_rings(zone.polygon))
    for obstacle in scenario.obstacles:
        placemark = _sub(folder, "Placemark")
        _sub(
            placemark,
            "name",
            f"Препятствие {obstacle.name or obstacle.id}, {obstacle.height_m:.0f} м",
        )
        _sub(placemark, "styleUrl", "#obstacle")
        _extended(placemark, {"kind": "obstacle", "height_m": obstacle.height_m})
        if obstacle.polygon is not None:
            _polygon(placemark, _geo_rings(obstacle.polygon))
        elif obstacle.position is not None:
            point = _sub(placemark, "Point")
            _sub(point, "altitudeMode", "clampToGround")
            _sub(point, "coordinates", f"{obstacle.position.lon:.8f},{obstacle.position.lat:.8f},0")
    for site in scenario.sites:
        placemark = _sub(folder, "Placemark")
        _sub(placemark, "name", site.name or site.id)
        _sub(placemark, "description", f"Тип площадки: {site.kind.value}")
        point = _sub(placemark, "Point")
        _sub(point, "altitudeMode", "clampToGround")
        _sub(point, "coordinates", f"{site.position.lon:.8f},{site.position.lat:.8f},0")


def mission_to_kml(
    result: PipelineResult, scenario: Scenario, projector: Projector
) -> ET.ElementTree:
    """Собрать KML: полётное задание или отчёт о невыполнимости.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.

    Returns:
        Дерево XML, готовое к записи.
    """
    mission = result.mission
    # Пространство имён — обычным атрибутом: дерево в памяти и записанный файл
    # совпадают по именам элементов.
    root = ET.Element("kml", {"xmlns": _KML_NS})
    document = _sub(root, "Document")
    _sub(document, "name", scenario.name or mission.scenario_id)
    stub_note = (
        " ВНИМАНИЕ: заглушки на стадиях "
        + ", ".join(stage.value for stage in mission.stub_stages)
        + "."
        if mission.stub_stages
        else ""
    )
    if mission.is_feasible() and mission.metrics is not None:
        description = (
            f"План выполним. Режим {mission.mode.value}, алгоритм {mission.algorithm}. "
            f"Срок {mission.metrics.makespan_s / 60:.1f} мин, налёт "
            f"{mission.metrics.total_flight_time_s / 60:.1f} мин, вылетов "
            f"{mission.metrics.sortie_count}. Высоты — над поверхностью, режим "
            f"{mission.altitude_mode.value}.{stub_note}"
        )
    else:
        description = (
            "ПЛАН НЕВЫПОЛНИМ. "
            + " ".join(reason.message for reason in mission.reasons[:5])
            + stub_note
        )
    _sub(document, "description", description)
    _styles(document)

    if mission.is_feasible():
        routes = _sub(document, "Folder")
        _sub(routes, "name", "Полётные задания")
        by_vehicle: dict[tuple[str, int], ET.Element] = {}
        for sortie in mission.sorties:
            key = (sortie.vehicle_id, sortie.vehicle_instance)
            if key not in by_vehicle:
                vehicle_folder = _sub(routes, "Folder")
                _sub(vehicle_folder, "name", f"{sortie.vehicle_id} #{sortie.vehicle_instance + 1}")
                by_vehicle[key] = vehicle_folder
            _sortie_folder(
                by_vehicle[key], sortie, result, scenario, projector, mission.altitude_mode.value
            )
    else:
        reasons = _sub(document, "Folder")
        _sub(reasons, "name", "Причины невыполнимости")
        for reason in mission.reasons:
            placemark = _sub(reasons, "Placemark")
            _sub(placemark, "name", reason.code)
            _sub(placemark, "description", reason.message)
            if reason.geometry is not None:
                _sub(placemark, "styleUrl", "#violation")
                line = _sub(placemark, "LineString")
                _sub(line, "altitudeMode", "clampToGround")
                _sub(
                    line,
                    "coordinates",
                    " ".join(
                        f"{lon:.8f},{lat:.8f},0"
                        for lon, lat in projector.coords_to_geo(
                            (point.x_m, point.y_m) for point in reason.geometry.points
                        )
                    ),
                )
        advice = _sub(document, "Folder")
        _sub(advice, "name", "Рекомендации")
        for item in mission.recommendations:
            placemark = _sub(advice, "Placemark")
            _sub(placemark, "name", item.kind.value)
            _sub(placemark, "description", item.message)
            if item.position is not None:
                geo = projector.to_geo(item.position)
                point = _sub(placemark, "Point")
                _sub(point, "altitudeMode", "clampToGround")
                _sub(point, "coordinates", f"{geo.lon:.8f},{geo.lat:.8f},0")

    _context_folder(document, scenario)
    return ET.ElementTree(root)


def write_kml(result: PipelineResult, scenario: Scenario, projector: Projector, path: Path) -> Path:
    """Записать KML на диск.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь файла.

    Returns:
        Путь записанного файла.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = mission_to_kml(result, scenario, projector)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


__all__ = ["mission_to_kml", "write_kml"]
