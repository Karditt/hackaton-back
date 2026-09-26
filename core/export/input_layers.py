"""Сценарий в формате входа заказчика: три слоя KML и параметры задания JSON.

Обратная сторона `core/geo/kml_import.py` (docs/input_formats.md): сценарий,
выгруженный сюда и прочитанный импортом, — тот же сценарий, что и был. Так
тестовые сценарии живут в том же виде, в каком заказчик отдаёт данные, и их
можно открыть в ГИС и поправить там.

- **Область** — `Placemark` на кусок, `Polygon` с отверстиями, без высот.
- **Зоны** — предложенный формат (§3.2): машинные поля высот и окон
  (`zone_id`, `zone_kind`, `alt_datum`, `alt_min_m`, `alt_max_m`,
  `active_windows`) и человеческий текст в `description`.
- **Препятствия** — «3d примитив» заказчика: точка или контур с высотой `Z`
  над землёй, `relativeToGround` и `extrude`.
- **Параметры задания** — всё, кроме трёх слоёв: флот, площадки, граница
  пространства, коридор, GSD, ветер, сроки, ТТХ пользователя.

Координаты пишутся без округления (`repr` числа): округлённая выгрузка дала
бы другой сценарий.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.schemas import GeoPoint, GeoPolygon, NoFlyZone, Obstacle, Scenario

KML_NS = "http://www.opengis.net/kml/2.2"

#: Открытый конец интервала времени (ISO 8601-2): окно без начала или без конца.
OPEN_END = ".."

#: Имена файлов комплекта по виду слоя.
LAYER_FILES = {
    "area": "area.kml",
    "zones": "zones.kml",
    "obstacles": "obstacles.kml",
    "params": "params.json",
}

#: Поля сценария, которые живут в слоях KML, а не в параметрах задания.
_LAYER_FIELDS = {"area", "no_fly_zones", "obstacles"}


def _element(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    element = ET.SubElement(parent, f"{{{KML_NS}}}{tag}")
    if text is not None:
        element.text = text
    return element


def _document(title: str) -> tuple[ET.Element, ET.Element]:
    """Корень KML и его `Document`."""
    ET.register_namespace("", KML_NS)
    root = ET.Element(f"{{{KML_NS}}}kml")
    document = _element(root, "Document")
    _element(document, "name", title)
    return root, document


def _to_bytes(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _ring_text(ring: Sequence[GeoPoint], height_m: float | None = None) -> str:
    """Кольцо в текст `<coordinates>`: замкнутое, без округления координат."""
    points = list(ring)
    if points and (points[0].lon, points[0].lat) != (points[-1].lon, points[-1].lat):
        points.append(points[0])
    suffix = "" if height_m is None else f",{height_m!r}"
    return " ".join(f"{point.lon!r},{point.lat!r}{suffix}" for point in points)


def _polygon(
    parent: ET.Element, polygon: GeoPolygon, height_m: float | None = None, holes: bool = True
) -> None:
    """`<Polygon>`: внешнее кольцо и, если нужно, отверстия."""
    element = _element(parent, "Polygon")
    if height_m is not None:
        _element(element, "extrude", "1")
        _element(element, "altitudeMode", "relativeToGround")
    outer = _element(_element(element, "outerBoundaryIs"), "LinearRing")
    _element(outer, "coordinates", _ring_text(polygon.outer, height_m))
    for hole in polygon.holes if holes else []:
        inner = _element(_element(element, "innerBoundaryIs"), "LinearRing")
        _element(inner, "coordinates", _ring_text(hole, height_m))


def _data(placemark: ET.Element, values: dict[str, str]) -> None:
    extended = _element(placemark, "ExtendedData")
    for name, value in values.items():
        item = _element(extended, "Data")
        item.set("name", name)
        _element(item, "value", value)


def _utc(moment: datetime) -> str:
    """Момент UTC в виде `2026-09-20T07:00:00Z`."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: float) -> str:
    """Высота для поля и подписи: целое без «.0»."""
    return f"{value:g}"


def area_kml(scenario: Scenario) -> bytes:
    """Область съёмки: `Placemark` на каждый кусок мультиполигона."""
    root, document = _document(f"Область съёмки — {scenario.name or scenario.id}")
    for index, polygon in enumerate(scenario.area.polygons, start=1):
        placemark = _element(document, "Placemark")
        _element(placemark, "name", f"Участок {index}")
        _polygon(placemark, polygon)
    return _to_bytes(root)


def _altitude_text(zone: NoFlyZone) -> str:
    """Высоты зоны словами — так, как их пишет заказчик."""
    low = zone.min_alt_m or 0.0
    bottom = "От земли" if low <= 0.0 else f"От {_number(low)} м AGL"
    if zone.max_alt_m is None:
        return "На всех высотах" if low <= 0.0 else f"{bottom} и выше"
    return f"{bottom} до {_number(zone.max_alt_m)} м AGL"


def _zone_placemark(document: ET.Element, zone: NoFlyZone) -> None:
    window = zone.active_from is not None or zone.active_to is not None
    windows = ""
    text = _altitude_text(zone)
    placemark = _element(document, "Placemark")
    _element(placemark, "name", zone.name or zone.id)
    if window:
        # Окно, открытое с одной стороны, — открытый интервал ISO 8601: «..».
        start = _utc(zone.active_from) if zone.active_from else OPEN_END
        end = _utc(zone.active_to) if zone.active_to else OPEN_END
        windows = f"{start}/{end}"
        bounds = [f"с {start}"] if zone.active_from else []
        bounds += [f"до {end}"] if zone.active_to else []
        text += f"; действует {' '.join(bounds)} UTC"
    _element(placemark, "description", text)
    if window:
        span = _element(placemark, "TimeSpan")
        if zone.active_from:
            _element(span, "begin", start)
        if zone.active_to:
            _element(span, "end", end)
    _data(
        placemark,
        {
            "zone_id": zone.id,
            "zone_kind": "temporary" if window else "prohibited",
            "alt_datum": "AGL",
            "alt_min_m": _number(zone.min_alt_m or 0.0),
            "alt_max_m": "" if zone.max_alt_m is None else _number(zone.max_alt_m),
            "active_windows": windows,
            "source_text": text,
        },
    )
    # Импорт берёт у зоны только внешний контур: отверстий в зонах нет.
    _polygon(placemark, zone.polygon, holes=False)


def zones_kml(scenario: Scenario) -> bytes | None:
    """Зоны ограничений в предложенном формате (§3.2); нет зон — None."""
    if not scenario.no_fly_zones:
        return None
    root, document = _document(f"Зоны ограничений — {scenario.name or scenario.id}")
    for zone in scenario.no_fly_zones:
        _zone_placemark(document, zone)
    return _to_bytes(root)


def _obstacle_placemark(document: ET.Element, obstacle: Obstacle) -> None:
    placemark = _element(document, "Placemark")
    # Как у заказчика: «идентификатор тип» через пробел.
    _element(placemark, "name", f"{obstacle.id} {obstacle.name}".strip())
    _element(placemark, "description", f"Высота {_number(obstacle.height_m)} м над землёй")
    if obstacle.position is not None:
        point = _element(placemark, "Point")
        _element(point, "extrude", "1")
        _element(point, "altitudeMode", "relativeToGround")
        _element(
            point,
            "coordinates",
            f"{obstacle.position.lon!r},{obstacle.position.lat!r},{obstacle.height_m!r}",
        )
    elif obstacle.polygon is not None:
        _polygon(placemark, obstacle.polygon, height_m=obstacle.height_m, holes=False)


def obstacles_kml(scenario: Scenario) -> bytes | None:
    """Высотные препятствия «3d примитивами» заказчика; нет препятствий — None."""
    if not scenario.obstacles:
        return None
    root, document = _document(f"Высотные препятствия — {scenario.name or scenario.id}")
    for obstacle in scenario.obstacles:
        _obstacle_placemark(document, obstacle)
    return _to_bytes(root)


def scenario_params(scenario: Scenario) -> dict[str, Any]:
    """Параметры задания: всё, кроме трёх слоёв KML, в форме JSON контракта."""
    return scenario.model_dump(mode="json", exclude=_LAYER_FIELDS, exclude_none=True)


def export_input_layers(scenario: Scenario, directory: Path) -> dict[str, Path]:
    """Записать сценарий комплектом файлов в формате входа заказчика.

    Args:
        scenario: сценарий с координатами WGS84.
        directory: каталог комплекта; создаётся при необходимости.

    Returns:
        Записанные файлы по виду слоя: `area`, `zones`, `obstacles`, `params`.
        Слоя без объектов нет и в комплекте.
    """
    directory.mkdir(parents=True, exist_ok=True)
    contents: dict[str, bytes | None] = {
        "area": area_kml(scenario),
        "zones": zones_kml(scenario),
        "obstacles": obstacles_kml(scenario),
        "params": (
            json.dumps(scenario_params(scenario), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    }
    written: dict[str, Path] = {}
    for kind, content in contents.items():
        if content is None:
            continue
        path = directory / LAYER_FILES[kind]
        path.write_bytes(content)
        written[kind] = path
    return written


__all__ = [
    "LAYER_FILES",
    "area_kml",
    "export_input_layers",
    "obstacles_kml",
    "scenario_params",
    "zones_kml",
]
