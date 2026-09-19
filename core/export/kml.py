"""Экспорт в KML (OGC KML 2.2) с расширением Google `gx:Track`.

Соглашения формата (docs/export_format.md §4), которые здесь соблюдаются явно:

* Порядок дочерних элементов — как в схеме OGC `ogckml22.xsd`: схема задаёт
  последовательности, а не наборы. У Feature: `name`, `visibility`, `open`,
  `Snippet`, `description`, время, `styleUrl`, стили, `ExtendedData`; у Document
  потом — вложенные Feature. Нарушенный порядок строгие читатели отвергают,
  а нестрогие молча теряют элементы.
* Режим высоты — явно у каждой геометрии. Умолчание KML — `clampToGround`
  («положить на землю»), а высоты проекта — над поверхностью (AGL, заказчик,
  №10), то есть `relativeToGround`.
* Цвет — `aabbggrr` (альфа, синий, зелёный, красный), а не привычный `rrggbb`.
  Цвета заданы в вебе как `#rrggbb` и переводятся функцией `_kml_color`: так
  фазы в Google Earth и на карте веба одного цвета.
* Время — `dateTime` XML Schema в UTC с точностью до секунды (`common.utc_time`).
  `TimeStamp` — у ключевых точек, `TimeSpan` — у участков маршрута и строк
  расписания, `gx:Track` — положение борта во времени: Google Earth проигрывает
  по нему полёт на шкале времени. Если начало работ в сценарии не задано,
  абсолютного времени нет: элементов времени и трека нет, моменты подписаны
  «T+чч:мм:сс», и это сказано в описании документа.
* Полигоны: внешнее кольцо против часовой стрелки, отверстия — по часовой
  (справочник KML: Google Earth заливает только лицевую сторону).
* `gx:coord` — «долгота широта высота» через пробелы, а `coordinates` —
  тройки через запятые, разделённые пробелами. Число `when` в треке равно
  числу `gx:coord`, и все `when` идут перед всеми `gx:coord` (схема
  `kml22gx.xsd`).

Структура: документ → «Расписание» → «Полётные задания» (борт → вылет →
ключевые точки, трек, маршрут по фазам, подобласти) → «Сценарий». Файл
собирается на весь план или на один борт (`board`) — индивидуальное полётное
задание (ТЗ). Невыполнимый план выгружается отчётом: причины с местами
нарушений и рекомендации, без маршрутов (инвариант 10).
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from core.export.boards import (
    Board,
    KeyPoint,
    KeyPointKind,
    select_boards,
    sortie_board_id,
    sortie_keypoints,
)
from core.export.common import (
    UTC_FORMAT,
    oriented_rings,
    relative_time,
    span_label,
    time_label,
    utc_moment,
    utc_time,
)
from core.export.geojson import FORMAT_VERSION
from core.export.schedule import ScheduleRow, schedule_rows
from core.geo.project import Projector
from core.schemas import (
    AltitudeMode,
    FlightPhase,
    GeoPolygon,
    MetricPolygon,
    Mission,
    PipelineResult,
    Scenario,
    Sortie,
)

_KML_NS = "http://www.opengis.net/kml/2.2"
_GX_NS = "http://www.google.com/kml/ext/2.2"

_RELATIVE = AltitudeMode.RELATIVE_TO_GROUND.value
_CLAMP = AltitudeMode.CLAMP_TO_GROUND.value

#: Цвета фаз — те же, что на карте веба (web/src/features/map/missionStyles.ts).
_PHASE_RGB: dict[FlightPhase, str] = {
    FlightPhase.TAKEOFF: "#16a34a",
    FlightPhase.CLIMB: "#0ea5e9",
    FlightPhase.TRANSIT: "#94a3b8",
    FlightPhase.STRIP: "#dc2626",
    FlightPhase.TURN: "#f59e0b",
    FlightPhase.HOLD: "#c026d3",
    FlightPhase.DESCENT: "#0ea5e9",
    FlightPhase.LANDING: "#1d4ed8",
}

#: Названия фаз для имён участков.
_PHASE_TITLES: dict[FlightPhase, str] = {
    FlightPhase.TAKEOFF: "взлёт",
    FlightPhase.CLIMB: "набор высоты",
    FlightPhase.TRANSIT: "перелёт",
    FlightPhase.STRIP: "галс",
    FlightPhase.TURN: "разворот",
    FlightPhase.HOLD: "ожидание",
    FlightPhase.DESCENT: "снижение",
    FlightPhase.LANDING: "посадка",
}

#: Цвета ключевых точек: взлёт и посадка — как их фазы, съёмка — как галс.
_KEYPOINT_RGB: dict[KeyPointKind, str] = {
    KeyPointKind.TAKEOFF: "#16a34a",
    KeyPointKind.SURVEY_START: "#dc2626",
    KeyPointKind.SURVEY_END: "#7f1d1d",
    KeyPointKind.LANDING: "#1d4ed8",
}

#: Площадные слои: id стиля → (цвет #rrggbb, альфа линии, альфа заливки, ширина).
_AREA_STYLES: dict[str, tuple[str, int, int, int]] = {
    "area": ("#2563eb", 0xFF, 0x1A, 2),
    "cell": ("#2563eb", 0x99, 0x14, 1),
    "maneuver": ("#f59e0b", 0xCC, 0x33, 1),
    "nfz": ("#dc2626", 0xFF, 0x55, 2),
    "airspace": ("#7c3aed", 0xFF, 0x00, 2),
    "obstacle": ("#7f1d1d", 0xFF, 0x99, 2),
}


def _kml_color(rgb: str, alpha: int = 0xFF) -> str:
    """Цвет `#rrggbb` и альфа 0..255 → цвет KML `aabbggrr`."""
    value = rgb.removeprefix("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"{alpha:02x}{blue}{green}{red}".lower()


def _sub(
    parent: ET.Element, tag: str, text: str | None = None, attrib: dict[str, str] | None = None
) -> ET.Element:
    """Создать дочерний элемент с текстом."""
    element = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        element.text = text
    return element


def _extended(parent: ET.Element, values: dict[str, object]) -> None:
    """Атрибуты в ExtendedData — машиночитаемо, как свойства в GeoJSON."""
    present = {name: value for name, value in values.items() if value is not None}
    if not present:
        return
    data = _sub(parent, "ExtendedData")
    for name, value in present.items():
        item = _sub(data, "Data", attrib={"name": name})
        _sub(item, "value", str(value))


def _feature(
    parent: ET.Element,
    tag: str,
    name: str,
    *,
    open_: bool | None = None,
    snippet: str | None = None,
    description: str | None = None,
    when: str | None = None,
    span: tuple[str | None, str | None] | None = None,
    style: str | None = None,
    data: dict[str, object] | None = None,
) -> ET.Element:
    """Feature KML с общими полями в порядке схемы OGC (`AbstractFeatureType`).

    Args:
        parent: родительский элемент.
        tag: `Placemark`, `Folder` или `Document`.
        name: имя.
        open_: раскрыт ли контейнер в списке Google Earth.
        snippet: краткая строка под именем в списке; "" — не показывать ничего.
        description: описание (текст или HTML).
        when: момент `TimeStamp`, UTC.
        span: интервал `TimeSpan` (начало, конец), UTC; край может быть None.
        style: id стиля без «#».
        data: атрибуты `ExtendedData`.

    Returns:
        Созданный элемент: геометрию или вложенные Feature добавляет вызывающий.
    """
    element = _sub(parent, tag)
    _sub(element, "name", name)
    if open_ is not None:
        _sub(element, "open", "1" if open_ else "0")
    if snippet is not None:
        _sub(element, "Snippet", snippet, {"maxLines": "1" if snippet else "0"})
    if description is not None:
        _sub(element, "description", description)
    if when is not None:
        _sub(_sub(element, "TimeStamp"), "when", when)
    elif span is not None and (span[0] or span[1]):
        time_span = _sub(element, "TimeSpan")
        if span[0]:
            _sub(time_span, "begin", span[0])
        if span[1]:
            _sub(time_span, "end", span[1])
    if style is not None:
        _sub(element, "styleUrl", f"#{style}")
    if data:
        _extended(element, data)
    return element


def _coordinates(points: list[tuple[float, float, float]]) -> str:
    """Тройки «долгота,широта,высота» через пробел — формат `coordinates`."""
    return " ".join(f"{lon:.8f},{lat:.8f},{alt:.1f}" for lon, lat, alt in points)


def _point(parent: ET.Element, lon: float, lat: float, alt_m: float, mode: str) -> None:
    """Точка; поднятая над землёй соединяется с поверхностью линией (`extrude`)."""
    point = _sub(parent, "Point")
    if mode != _CLAMP and alt_m > 0.0:
        _sub(point, "extrude", "1")
    _sub(point, "altitudeMode", mode)
    _sub(point, "coordinates", _coordinates([(lon, lat, alt_m)]))


def _line(parent: ET.Element, points: list[tuple[float, float, float]], mode: str) -> None:
    """Ломаная; прижатая к земле повторяет рельеф (`tessellate`)."""
    line = _sub(parent, "LineString")
    if mode == _CLAMP:
        _sub(line, "tessellate", "1")
    _sub(line, "altitudeMode", mode)
    _sub(line, "coordinates", _coordinates(points))


def _polygon(
    parent: ET.Element,
    rings: list[list[tuple[float, float]]],
    height_m: float | None = None,
) -> None:
    """Полигон: на земле либо поднятый на высоту и вытянутый к земле (препятствие).

    Args:
        parent: Placemark.
        rings: внешнее кольцо и отверстия, (долгота, широта).
        height_m: высота над поверхностью, метры; None — полигон на земле.
    """
    polygon = _sub(parent, "Polygon")
    if height_m is None:
        _sub(polygon, "tessellate", "1")
        _sub(polygon, "altitudeMode", _CLAMP)
        alt = 0.0
    else:
        _sub(polygon, "extrude", "1")
        _sub(polygon, "altitudeMode", _RELATIVE)
        alt = height_m
    for index, ring in enumerate(oriented_rings(rings)):
        boundary = _sub(polygon, "outerBoundaryIs" if index == 0 else "innerBoundaryIs")
        _sub(
            _sub(boundary, "LinearRing"),
            "coordinates",
            _coordinates([(lon, lat, alt) for lon, lat in ring]),
        )


def _geo_rings(polygon: GeoPolygon) -> list[list[tuple[float, float]]]:
    return [[(point.lon, point.lat) for point in ring] for ring in [polygon.outer, *polygon.holes]]


def _metric_rings(polygon: MetricPolygon, projector: Projector) -> list[list[tuple[float, float]]]:
    return [
        projector.coords_to_geo((point.x_m, point.y_m) for point in ring)
        for ring in [polygon.outer, *polygon.holes]
    ]


def _styles(document: ET.Element) -> None:
    """Стили: фазы, ключевые точки (обычный и подсвеченный вид), трек, слои."""
    for phase, rgb in _PHASE_RGB.items():
        style = _sub(document, "Style", attrib={"id": f"phase-{phase.value}"})
        line = _sub(style, "LineStyle")
        _sub(line, "color", _kml_color(rgb, 0x99 if phase is FlightPhase.TRANSIT else 0xF2))
        _sub(line, "width", "3" if phase is FlightPhase.STRIP else "2")

    # Подпись ключевой точки на карте скрыта (иначе у площадки десятки
    # наложенных подписей) и появляется при наведении — через StyleMap.
    for kind, rgb in _KEYPOINT_RGB.items():
        for variant, icon_scale, label_scale in (("normal", "0.8", "0"), ("highlight", "1.1", "1")):
            style = _sub(document, "Style", attrib={"id": f"kp-{kind.value}-{variant}"})
            icon = _sub(style, "IconStyle")
            _sub(icon, "color", _kml_color(rgb))
            _sub(icon, "scale", icon_scale)
            _sub(_sub(style, "LabelStyle"), "scale", label_scale)
        style_map = _sub(document, "StyleMap", attrib={"id": f"kp-{kind.value}"})
        for key in ("normal", "highlight"):
            pair = _sub(style_map, "Pair")
            _sub(pair, "key", key)
            _sub(pair, "styleUrl", f"#kp-{kind.value}-{key}")

    # Линия трека прозрачна: маршрут уже нарисован участками по фазам, от трека
    # нужна метка, которая движется по шкале времени.
    track = _sub(document, "Style", attrib={"id": "track"})
    icon = _sub(track, "IconStyle")
    _sub(icon, "color", _kml_color("#111827"))
    _sub(icon, "scale", "1.2")
    track_line = _sub(track, "LineStyle")
    _sub(track_line, "color", _kml_color("#111827", 0x00))
    _sub(track_line, "width", "1")

    for style_id, (rgb, line_alpha, fill_alpha, width) in _AREA_STYLES.items():
        style = _sub(document, "Style", attrib={"id": style_id})
        if style_id == "obstacle":
            icon = _sub(style, "IconStyle")
            _sub(icon, "color", _kml_color(rgb))
        line = _sub(style, "LineStyle")
        _sub(line, "color", _kml_color(rgb, line_alpha))
        _sub(line, "width", str(width))
        poly = _sub(style, "PolyStyle")
        _sub(poly, "color", _kml_color(rgb, fill_alpha))
        if fill_alpha == 0:
            _sub(poly, "fill", "0")

    for style_id, rgb, scale in (("site", "#facc15", "1.1"), ("recommended", "#2563eb", "1.2")):
        style = _sub(document, "Style", attrib={"id": style_id})
        icon = _sub(style, "IconStyle")
        _sub(icon, "color", _kml_color(rgb))
        _sub(icon, "scale", scale)

    violation = _sub(document, "Style", attrib={"id": "violation"})
    line = _sub(violation, "LineStyle")
    _sub(line, "color", _kml_color("#dc2626"))
    _sub(line, "width", "6")


def _schedule_table(rows: list[ScheduleRow], start_time: datetime | None) -> str:
    """Расписание HTML-таблицей — для описания папки в Google Earth."""
    header = ("Борт", "Вылет", "Взлёт", "Посадка", "Площадки", "Сдвиг, с", "Эшелон, м")
    lines = ["<table border='1' cellpadding='2' cellspacing='0'>"]
    lines.append("<tr>" + "".join(f"<th>{item}</th>" for item in header) + "</tr>")
    for row in rows:
        cells = (
            row.board_id,
            str(row.trip_index + 1),
            time_label(start_time, row.start_s),
            time_label(start_time, row.end_s),
            f"{row.takeoff_site_id} → {row.landing_site_id}",
            str(row.delay_s),
            str(row.transit_altitude_m),
        )
        lines.append("<tr>" + "".join(f"<td>{html.escape(item)}</td>" for item in cells) + "</tr>")
    lines.append("</table>")
    return "".join(lines)


def _schedule_folder(
    parent: ET.Element, rows: list[ScheduleRow], start_time: datetime | None
) -> None:
    """Папка «Расписание»: таблица в описании и строка на вылет.

    Строка расписания — Placemark без геометрии (схема KML это допускает): в
    Google Earth это пункт списка с интервалом времени, в QGIS — запись
    таблицы атрибутов с колонками расписания.
    """
    if not rows:
        return
    first = rows[0].start_s
    last = max(row.end_s for row in rows)
    folder = _feature(
        parent,
        "Folder",
        "Расписание",
        open_=False,
        snippet=f"Вылетов {len(rows)} · {span_label(start_time, first, last)}",
        description=_schedule_table(rows, start_time),
    )
    for row in rows:
        _feature(
            folder,
            "Placemark",
            f"{span_label(start_time, row.start_s, row.end_s)} · {row.board_id} · "
            f"вылет {row.trip_index + 1}",
            description=(
                f"Взлёт — площадка {row.takeoff_site_id}, посадка — {row.landing_site_id}. "
                f"Полётное время {row.duration_min} мин, эшелон перелёта "
                f"{row.transit_altitude_m} м AGL, старт сдвинут на {row.delay_s} с."
            ),
            span=(row.start_time_utc, row.end_time_utc),
            data={"kind": "schedule_entry", **row.as_dict()},
        )


def _keypoint_placemark(
    parent: ET.Element,
    sortie: Sortie,
    point: KeyPoint,
    lon: float,
    lat: float,
    start_time: datetime | None,
) -> None:
    """Ключевая точка вылета: точка с высотой и моментом."""
    where = f"площадка {point.site_id}" if point.site_id else f"подобласть {point.cell_id}"
    utc = utc_time(start_time, point.time_s)
    moment = f"{utc} ({relative_time(point.time_s)})" if utc else relative_time(point.time_s)
    placemark = _feature(
        parent,
        "Placemark",
        f"{point.title} · {time_label(start_time, point.time_s)}",
        description=f"{point.title}: {where}, высота {point.alt_m:.0f} м AGL, время {moment}.",
        when=utc,
        style=f"kp-{point.kind.value}",
        data={
            "kind": "keypoint",
            "keypoint": point.kind.value,
            "sortie_id": sortie.id,
            "board_id": sortie_board_id(sortie),
            "cell_id": point.cell_id,
            "site_id": point.site_id,
            "altitude_agl_m": round(point.alt_m, 1),
            "time_s": round(point.time_s, 1),
            "time_utc": utc,
        },
    )
    _point(placemark, lon, lat, point.alt_m, _RELATIVE)


def _track_placemark(
    parent: ET.Element,
    sortie: Sortie,
    geo: list[tuple[float, float]],
    start_time: datetime,
) -> None:
    """Трек `gx:Track`: положение борта в каждый момент вылета.

    Точки маршрута идут подряд по времени; на стыке участков одна и та же точка
    повторяется в тот же момент, а после округления до секунды совпадать могут
    и соседние точки. Такие точки схлопываются в одну — последнюю: момент в
    треке строго возрастает.
    """
    whens: list[str] = []
    coords: list[str] = []
    waypoints = [waypoint for leg in sortie.legs for waypoint in leg.waypoints]
    for (lon, lat), waypoint in zip(geo, waypoints, strict=True):
        moment = utc_moment(start_time, waypoint.time_s).strftime(UTC_FORMAT)
        coord = f"{lon:.8f} {lat:.8f} {waypoint.alt_m:.1f}"
        if whens and moment == whens[-1]:
            coords[-1] = coord
            continue
        whens.append(moment)
        coords.append(coord)

    placemark = _feature(
        parent,
        "Placemark",
        "Трек 4D",
        description=(
            f"Положение борта во времени: {len(whens)} точек, высоты AGL. Метка движется "
            "по шкале времени Google Earth."
        ),
        style="track",
        data={"kind": "track", "sortie_id": sortie.id, "board_id": sortie_board_id(sortie)},
    )
    track = _sub(placemark, "gx:Track")
    _sub(track, "altitudeMode", _RELATIVE)
    for moment in whens:
        _sub(track, "when", moment)
    for coord in coords:
        _sub(track, "gx:coord", coord)


def _sortie_folder(
    parent: ET.Element,
    sortie: Sortie,
    row: ScheduleRow | None,
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
) -> None:
    """Папка одного вылета: ключевые точки, трек, маршрут по фазам, подобласти."""
    start_time = scenario.start_time
    schedule = (
        row.as_dict() if row else {"sortie_id": sortie.id, "board_id": sortie_board_id(sortie)}
    )
    folder = _feature(
        parent,
        "Folder",
        f"Вылет {sortie.trip_index + 1} · "
        f"{span_label(start_time, sortie.start_time_s, sortie.end_time_s())}",
        open_=False,
        description=(
            f"Взлёт — площадка {sortie.takeoff_site_id}, посадка — {sortie.landing_site_id}. "
            f"Длительность {sortie.duration_s / 60:.1f} мин, дистанция "
            f"{sortie.distance_m / 1000:.1f} км, съёмка на {sortie.survey_altitude_m:.0f} м AGL, "
            f"перелёт на {sortie.transit_altitude_m:.0f} м AGL. "
            f"Подобласти: {', '.join(sortie.cell_ids)}."
        ),
        data={
            **schedule,
            "kind": "sortie",
            "survey_altitude_m": round(sortie.survey_altitude_m, 1),
            "distance_m": round(sortie.distance_m, 1),
            "energy_fraction_used": round(sortie.energy_fraction_used, 3),
            "cell_ids": " ".join(sortie.cell_ids),
        },
    )

    # Одно преобразование координат на вылет: им пользуются и трек, и участки.
    waypoints = [waypoint for leg in sortie.legs for waypoint in leg.waypoints]
    geo = projector.coords_to_geo((waypoint.x_m, waypoint.y_m) for waypoint in waypoints)

    for point in sortie_keypoints(sortie):
        lon, lat = projector.coords_to_geo([(point.x_m, point.y_m)])[0]
        _keypoint_placemark(folder, sortie, point, lon, lat, start_time)

    if start_time is not None:
        _track_placemark(folder, sortie, geo, start_time)

    legs_folder = _feature(folder, "Folder", "Маршрут по фазам", open_=False)
    offset = 0
    for index, leg in enumerate(sortie.legs):
        count = len(leg.waypoints)
        points = [
            (lon, lat, waypoint.alt_m)
            for (lon, lat), waypoint in zip(
                geo[offset : offset + count], leg.waypoints, strict=True
            )
        ]
        offset += count
        # Наземные фазы — одна точка в один момент на площадке: линии нет, их
        # моменты — ключевые точки «Взлёт» и «Посадка». Набор и снижение —
        # вертикальные линии над площадкой, они остаются.
        if len({(round(lon, 9), round(lat, 9), round(alt, 1)) for lon, lat, alt in points}) < 2:
            continue
        start_s, end_s = leg.waypoints[0].time_s, leg.waypoints[-1].time_s
        name = f"{index + 1:02d} · {_PHASE_TITLES[leg.phase]}"
        if leg.strip_id:
            name += f" {leg.strip_id}"
        placemark = _feature(
            legs_folder,
            "Placemark",
            name,
            span=(utc_time(start_time, start_s), utc_time(start_time, end_s)),
            style=f"phase-{leg.phase.value}",
            data={
                "kind": "leg",
                "sortie_id": sortie.id,
                "board_id": sortie_board_id(sortie),
                "phase": leg.phase.value,
                "cell_id": leg.cell_id,
                "strip_id": leg.strip_id,
                "length_m": round(leg.length_m, 1),
                "duration_s": round(leg.duration_s, 1),
                "ground_speed_mps": round(leg.ground_speed_mps, 2),
                "altitude_agl_m": round(leg.waypoints[-1].alt_m, 1),
                "start_s": round(start_s, 1),
                "end_s": round(end_s, 1),
            },
        )
        _line(placemark, points, _RELATIVE)

    decomposition = result.decomposition
    coverage = result.coverage
    if decomposition is None or coverage is None:
        return
    cells_folder = _feature(folder, "Folder", "Подобласти", open_=False)
    for cell_id in sortie.cell_ids:
        cell = decomposition.cell(cell_id)
        if cell is not None:
            placemark = _feature(
                cells_folder,
                "Placemark",
                f"Подобласть {cell_id}",
                description=f"Снять целиком на высоте {sortie.survey_altitude_m:.0f} м AGL.",
                style="cell",
                data={
                    "kind": "survey_cell",
                    "cell_id": cell_id,
                    "sortie_id": sortie.id,
                    "area_m2": round(cell.area_m2, 1),
                },
            )
            _polygon(placemark, _metric_rings(cell.polygon, projector))
        for item in coverage.cells:
            if item.cell_id != cell_id or item.vehicle_id != sortie.vehicle_id:
                continue
            for zone in item.maneuver_zones:
                placemark = _feature(
                    cells_folder,
                    "Placemark",
                    f"Зона манёвра {cell_id}",
                    description="Защитная зона разворота самолёта на концах галсов (№38).",
                    style="maneuver",
                    data={"kind": "maneuver_zone", "cell_id": cell_id, "sortie_id": sortie.id},
                )
                _polygon(placemark, _metric_rings(zone, projector))


def _board_folder(
    parent: ET.Element,
    board: Board,
    rows: dict[str, ScheduleRow],
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    open_: bool,
) -> None:
    """Папка борта: его вылеты в порядке взлёта."""
    start_time = scenario.start_time
    folder = _feature(
        parent,
        "Folder",
        board.label,
        open_=open_,
        description=(
            f"Вылетов {len(board.sorties)}, полётное время "
            f"{board.flight_time_s / 60:.1f} мин; "
            f"{span_label(start_time, board.first_takeoff_s, board.last_landing_s)}."
        ),
        data={"kind": "board", **board.summary()},
    )
    for sortie in board.sorties:
        _sortie_folder(folder, sortie, rows.get(sortie.id), result, scenario, projector)


def _context_folder(document: ET.Element, scenario: Scenario) -> None:
    """Слои сценария: область, зоны, пространство, препятствия, площадки."""
    folder = _feature(document, "Folder", "Сценарий", open_=False)
    for index, polygon in enumerate(scenario.area.polygons):
        name = "Область съёмки"
        if len(scenario.area.polygons) > 1:
            name += f", часть {index + 1}"
        placemark = _feature(
            folder, "Placemark", name, style="area", data={"kind": "survey_area", "part": index + 1}
        )
        _polygon(placemark, _geo_rings(polygon))

    for zone in scenario.no_fly_zones:
        low = f"от {zone.min_alt_m:.0f}" if zone.min_alt_m is not None else "от земли"
        high = f"до {zone.max_alt_m:.0f} м AGL" if zone.max_alt_m is not None else "без потолка"
        active_from = utc_time(zone.active_from, 0.0)
        active_to = utc_time(zone.active_to, 0.0)
        window = (
            f"действует {active_from or '…'} — {active_to or '…'}"
            if active_from or active_to
            else "действует всегда"
        )
        placemark = _feature(
            folder,
            "Placemark",
            f"Запретная зона {zone.name or zone.id}",
            description=f"Высоты {low} {high}; {window}.",
            span=(active_from, active_to),
            style="nfz",
            data={
                "kind": "no_fly_zone",
                "id": zone.id,
                "min_alt_m": zone.min_alt_m,
                "max_alt_m": zone.max_alt_m,
                "active_from": active_from,
                "active_to": active_to,
            },
        )
        _polygon(placemark, _geo_rings(zone.polygon))

    if scenario.airspace_boundary is not None:
        placemark = _feature(
            folder,
            "Placemark",
            "Граница воздушного пространства",
            style="airspace",
            data={"kind": "airspace_boundary"},
        )
        _polygon(placemark, _geo_rings(scenario.airspace_boundary))

    # Препятствие поднято на свою высоту над поверхностью и вытянуто к земле:
    # в Google Earth мачта видна вертикальной линией, здание — объёмом.
    for obstacle in scenario.obstacles:
        placemark = _feature(
            folder,
            "Placemark",
            f"Препятствие {obstacle.name or obstacle.id}, {obstacle.height_m:.0f} м",
            style="obstacle",
            data={"kind": "obstacle", "id": obstacle.id, "height_m": obstacle.height_m},
        )
        if obstacle.polygon is not None:
            _polygon(placemark, _geo_rings(obstacle.polygon), height_m=obstacle.height_m)
        elif obstacle.position is not None:
            _point(
                placemark,
                obstacle.position.lon,
                obstacle.position.lat,
                obstacle.height_m,
                _RELATIVE,
            )

    for site in scenario.sites:
        placemark = _feature(
            folder,
            "Placemark",
            site.name or site.id,
            description=f"Площадка {site.id}, тип {site.kind.value}.",
            style="site",
            data={"kind": "site", "site_id": site.id, "site_kind": site.kind.value},
        )
        _point(placemark, site.position.lon, site.position.lat, 0.0, _CLAMP)


def _report_folders(document: ET.Element, mission: Mission, projector: Projector) -> None:
    """Отчёт о невыполнимости: причины с местами нарушений и рекомендации."""
    reasons = _feature(document, "Folder", "Причины невыполнимости", open_=True)
    for reason in mission.reasons:
        placemark = _feature(
            reasons,
            "Placemark",
            reason.code,
            description=reason.message,
            style="violation" if reason.geometry is not None else None,
            data={"kind": "violation", "code": reason.code, "stage": reason.stage.value},
        )
        if reason.geometry is not None:
            geo = projector.coords_to_geo(
                (point.x_m, point.y_m) for point in reason.geometry.points
            )
            _line(placemark, [(lon, lat, 0.0) for lon, lat in geo], _CLAMP)

    advice = _feature(document, "Folder", "Рекомендации", open_=True)
    for item in mission.recommendations:
        placemark = _feature(
            advice,
            "Placemark",
            item.kind.value,
            description=item.message,
            style="recommended" if item.position is not None else None,
            data={"kind": "recommended_site", "recommendation": item.kind.value},
        )
        if item.position is not None:
            geo = projector.to_geo(item.position)
            _point(placemark, geo.lon, geo.lat, 0.0, _CLAMP)


def _document_description(
    mission: Mission, scenario: Scenario, boards: list[Board], board: str | None
) -> str:
    """Описание документа (HTML): итог, как читать время и высоты, состав папок."""
    lines: list[str] = []
    metrics = mission.metrics
    if mission.is_feasible() and metrics is not None:
        if board is not None and boards:
            item = boards[0]
            span = span_label(scenario.start_time, item.first_takeoff_s, item.last_landing_s)
            lines.append(
                f"<b>Полётное задание борта {html.escape(item.label)}.</b> "
                f"Вылетов {len(item.sorties)}, полётное время {item.flight_time_s / 60:.1f} мин, "
                f"{html.escape(span)}."
            )
            lines.append(
                f"Часть плана «{html.escape(scenario.name or scenario.id)}»: всего вылетов "
                f"{metrics.sortie_count}, бортов {metrics.vehicle_count}, срок "
                f"{metrics.makespan_s / 60:.1f} мин."
            )
        else:
            lines.append(
                f"<b>План выполним.</b> Режим {mission.mode.value}, алгоритм "
                f"{html.escape(mission.algorithm)}. Срок {metrics.makespan_s / 60:.1f} мин, "
                f"налёт {metrics.total_flight_time_s / 60:.1f} мин, вылетов "
                f"{metrics.sortie_count}, бортов {metrics.vehicle_count}."
            )
    else:
        lines.append("<b>ПЛАН НЕВЫПОЛНИМ.</b> Маршрутов нет — частичных планов не бывает.")
        lines.extend(html.escape(reason.message) for reason in mission.reasons[:5])

    if scenario.start_time is not None:
        lines.append(
            f"Время — UTC, начало работ {utc_time(scenario.start_time, 0.0)}. Полёт проигрывается "
            "на шкале времени: у участков TimeSpan, у ключевых точек TimeStamp, у вылета — трек "
            "gx:Track."
        )
    else:
        lines.append(
            "<b>Время относительное:</b> начало работ в сценарии не задано (start_time), поэтому "
            "меток времени и треков нет. Моменты подписаны «T+чч:мм:сс» от начала работ."
        )
    lines.append("Высоты — метры над поверхностью (AGL), altitudeMode = relativeToGround.")
    if mission.is_feasible():
        lines.append(
            "Папки: «Расписание» — вылеты по времени; «Полётные задания» — борт → вылет → "
            "ключевые точки, трек, маршрут по фазам, подобласти; «Сценарий» — исходные слои."
        )
    if mission.stub_stages:
        stages = ", ".join(stage.value for stage in mission.stub_stages)
        lines.append(f"ВНИМАНИЕ: заглушки на стадиях {stages} — план не окончательный.")
    lines.append(f"Формат {FORMAT_VERSION}, описание — docs/export_format.md.")
    return "<br>".join(lines)


def mission_to_kml(
    result: PipelineResult, scenario: Scenario, projector: Projector, board: str | None = None
) -> ET.ElementTree:
    """Собрать KML: полётное задание или отчёт о невыполнимости.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        board: идентификатор борта (`board_id`) — индивидуальное задание
            одного борта; None — весь план.

    Returns:
        Дерево XML, готовое к записи.

    Raises:
        ExportError: борта с таким идентификатором в плане нет.
    """
    mission = result.mission
    boards = select_boards(mission, board)
    rows = schedule_rows(mission, scenario, board)

    # Пространства имён — обычными атрибутами: дерево в памяти и записанный
    # файл совпадают по именам элементов, а префикс `gx:` пишется как есть.
    root = ET.Element("kml", {"xmlns": _KML_NS, "xmlns:gx": _GX_NS})
    title = scenario.name or mission.scenario_id
    if board is not None and boards:
        title = f"{title} — {boards[0].label}"
    document = _feature(
        root,
        "Document",
        title,
        open_=True,
        snippet="",
        description=_document_description(mission, scenario, boards, board),
    )
    _styles(document)
    _extended(
        document,
        {
            "format": FORMAT_VERSION,
            "status": mission.status.value,
            "mission_id": mission.id,
            "scenario_id": mission.scenario_id,
            "board_id": board,
            "mode": mission.mode.value,
            "algorithm": mission.algorithm,
            "altitude_reference": "AGL",
            "time_reference": "utc" if scenario.start_time is not None else "relative",
            "start_time": utc_time(scenario.start_time, 0.0),
        },
    )

    if mission.is_feasible():
        _schedule_folder(document, rows, scenario.start_time)
        routes = _feature(document, "Folder", "Полётные задания", open_=True)
        by_sortie = {row.sortie_id: row for row in rows}
        for item in boards:
            _board_folder(
                routes, item, by_sortie, result, scenario, projector, open_=board is not None
            )
    else:
        _report_folders(document, mission, projector)

    _context_folder(document, scenario)
    return ET.ElementTree(root)


def kml_to_bytes(tree: ET.ElementTree) -> bytes:
    """Сериализовать KML: UTF-8 с объявлением XML, с отступами."""
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)


def write_kml(
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    path: Path,
    board: str | None = None,
) -> Path:
    """Записать KML на диск.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь файла.
        board: идентификатор борта; None — весь план.

    Returns:
        Путь записанного файла.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(kml_to_bytes(mission_to_kml(result, scenario, projector, board)))
    return path


__all__ = ["kml_to_bytes", "mission_to_kml", "write_kml"]
