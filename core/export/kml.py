"""Экспорт плана в KML (OGC 2.2).

Режим высоты выставляется ЯВНО. `clampToGround`, `relativeToGround` и
`absolute` дают три разных смысла одной и той же цифре, и умолчание KML —
`clampToGround`, то есть «положить на землю». Без явного указания план,
рассчитанный на 150 м, откроется прижатым к рельефу, и ошибка будет не видна
глазом (PROJECT_BRIEF.md §7).

Проект работает без ЦМР, поэтому режим по умолчанию — `relativeToGround`,
а базой служит точка старта (`assumptions.TERRAIN_MODEL`).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from core.geo.project import Projector
from core.schemas import FlightPhase, Mission, Scenario, Sortie

_KML_NS = "http://www.opengis.net/kml/2.2"

#: Цвета фаз в формате KML aabbggrr (альфа, синий, зелёный, красный).
_PHASE_COLORS: dict[FlightPhase, str] = {
    FlightPhase.TAKEOFF: "ff00ff00",
    FlightPhase.CLIMB: "ff20c0ff",
    FlightPhase.TRANSIT: "ff909090",
    FlightPhase.STRIP: "ffff3030",
    FlightPhase.TURN: "ff00a5ff",
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
    """Объявить стиль линии для каждой фазы полёта."""
    for phase, color in _PHASE_COLORS.items():
        style = ET.SubElement(document, "Style", {"id": f"phase-{phase.value}"})
        line = _sub(style, "LineStyle")
        _sub(line, "color", color)
        _sub(line, "width", "3" if phase is FlightPhase.STRIP else "2")


def _sortie_folder(
    parent: ET.Element, sortie: Sortie, projector: Projector, altitude_mode: str
) -> None:
    """Папка одного вылета: по Placemark на каждый участок."""
    folder = _sub(parent, "Folder")
    _sub(
        folder,
        "name",
        f"{sortie.vehicle_id} #{sortie.vehicle_instance} · вылет {sortie.trip_index + 1}",
    )
    _sub(
        folder,
        "description",
        f"Длительность {sortie.duration_s / 60:.1f} мин, "
        f"дистанция {sortie.distance_m / 1000:.1f} км, "
        f"галсов {len(sortie.strip_ids)}",
    )

    for index, leg in enumerate(sortie.legs):
        coords: list[str] = []
        for waypoint in leg.waypoints:
            geo = projector.xy_to_geo(waypoint.x_m, waypoint.y_m, waypoint.alt_m)
            coords.append(f"{geo.lon:.8f},{geo.lat:.8f},{waypoint.alt_m:.1f}")
        if len({item.rsplit(",", 1)[0] for item in coords}) < 2:
            continue

        placemark = _sub(folder, "Placemark")
        _sub(placemark, "name", f"{index + 1:02d} · {leg.phase.value}")
        _sub(
            placemark,
            "description",
            f"длина {leg.length_m:.0f} м, время {leg.duration_s:.0f} с, "
            f"путевая {leg.ground_speed_mps:.1f} м/с",
        )
        _sub(placemark, "styleUrl", f"#phase-{leg.phase.value}")
        line = _sub(placemark, "LineString")
        _sub(line, "extrude", "0")
        _sub(line, "tessellate", "0")
        _sub(line, "altitudeMode", altitude_mode)
        _sub(line, "coordinates", " ".join(coords))


def mission_to_kml(mission: Mission, scenario: Scenario, projector: Projector) -> ET.ElementTree:
    """Собрать KML-документ полётного задания.

    Args:
        mission: готовый план.
        scenario: исходный сценарий (площадки идут отдельным слоем).
        projector: преобразователь рабочей проекции в WGS84.

    Returns:
        Дерево XML, готовое к записи.
    """
    # Пространство имён задаётся обычным атрибутом, а не через register_namespace:
    # так дерево в памяти и записанный файл совпадают по именам элементов, и
    # обход дерева не зависит от того, прошло оно сериализацию или нет.
    root = ET.Element("kml", {"xmlns": _KML_NS})
    document = _sub(root, "Document")
    _sub(document, "name", scenario.name or mission.scenario_id)

    stub_note = (
        " ВНИМАНИЕ: план рассчитан с заглушками на стадиях "
        + ", ".join(stage.value for stage in mission.stub_stages)
        + "."
        if mission.stub_stages
        else ""
    )
    _sub(
        document,
        "description",
        f"Алгоритм {mission.algorithm}, α={mission.alpha}. "
        f"Makespan {mission.metrics.makespan_s / 60:.1f} мин, "
        f"суммарный налёт {mission.metrics.total_flight_time_s / 60:.1f} мин. "
        f"Высоты — режим {mission.altitude_mode.value}.{stub_note}",
    )
    _styles(document)

    routes = _sub(document, "Folder")
    _sub(routes, "name", "Маршруты")
    for sortie in mission.sorties:
        _sortie_folder(routes, sortie, projector, mission.altitude_mode.value)

    sites = _sub(document, "Folder")
    _sub(sites, "name", "Площадки")
    for site in scenario.sites:
        placemark = _sub(sites, "Placemark")
        _sub(placemark, "name", site.name or site.id)
        _sub(placemark, "description", f"Тип площадки: {site.kind.value}")
        point = _sub(placemark, "Point")
        _sub(point, "altitudeMode", "clampToGround")
        _sub(point, "coordinates", f"{site.position.lon:.8f},{site.position.lat:.8f},0")

    return ET.ElementTree(root)


def write_kml(mission: Mission, scenario: Scenario, projector: Projector, path: Path) -> Path:
    """Записать KML на диск.

    Args:
        mission: готовый план.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь файла.

    Returns:
        Путь записанного файла.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = mission_to_kml(mission, scenario, projector)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


__all__ = ["mission_to_kml", "write_kml"]
