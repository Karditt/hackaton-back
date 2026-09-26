"""Чтение входных слоёв KML заказчика: область, зоны, препятствия (E1).

Правила разбора и предложенный формат зон — [`docs/input_formats.md`](../../docs/input_formats.md).
Здесь только их реализация, решения не принимаются заново.

Три вещи, которые определяют устройство модуля:

1. **Геометрические решения принимаются в метрах** (инвариант 1). Импорт — это
   и есть граница, на которой WGS84 превращается в метры: вырожденность,
   площадь и буфер линейных препятствий считаются в рабочей проекции, а наружу
   отдаётся снова WGS84, как того требует контракт сценария.
2. **Дефекты промышленной выгрузки — норма.** В образцах заказчика есть
   самопересечения, кольца нулевой площади, значение `altitudeMode` не из
   словаря KML, записи с координатами `0,0` и опечатки распознавания. Импорт,
   который на них падает, бесполезен; импорт, который молча их правит, опасен.
   Поэтому каждое исправление и каждый выброшенный объект попадают в отчёт.
3. **Высоты зон в файле заказчика — свободный текст** («От земли до FL140»).
   Разбор вынесен в `parse_altitude_text` и покрыт тестами отдельно от KML:
   это самая хрупкая часть, и ломаться она должна в одном месте.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from shapely.geometry import LineString, Polygon, box
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.errors import GeometryError
from core.geo.crs import pick_working_crs
from core.geo.project import Projector, polygons_of
from core.schemas import (
    GeoMultiPolygon,
    GeoPoint,
    GeoPolygon,
    ImportReport,
    LayerStats,
    NoFlyZone,
    Obstacle,
    PlanWarning,
    Severity,
    Stage,
)

#: Высота над уровнем моря по умолчанию: рельефа нет, ЦМР не подключена (E1, P1).
_NO_TERRAIN_M = 0.0

#: Значения `altitudeMode`, при которых Z — высота объекта над землёй.
_AGL_MODES = frozenset({"relativetoground"})

#: Значения `altitudeMode`, при которых Z — отметка верхушки над уровнем моря.
#: `relativetoseafloor` — не из словаря KML, но по величинам Z это то же,
#: что `absolute` (docs/input_formats.md §4.2); в файле заказчика оно ещё и
#: записано с хвостовым пробелом.
_AMSL_MODES = frozenset({"absolute", "relativetoseafloor"})

#: Значения `altitudeMode`, которых в стандарте KML нет: принимаем, но говорим вслух.
_NONSTANDARD_MODES = frozenset({"relativetoseafloor"})

#: Типы зон заказчика → постоянная она или временная.
_ZONE_KINDS = {
    "запретная_зона": ("prohibited", True),
    "пост_ограничение": ("restricted", True),
    "врем_ограничение": ("temporary", False),
}

#: Типы зон предложенного формата (`zone_kind`, docs/input_formats.md §3.2).
_MACHINE_ZONE_KINDS = {
    "prohibited": ("prohibited", True),
    "restricted": ("restricted", True),
    "temporary": ("temporary", False),
}

_FL_RE = re.compile(r"FL\s*(\d+)", re.IGNORECASE)
_METERS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*м\b")
_GROUND_RE = re.compile(r"\bземл", re.IGNORECASE)
_ALL_ALTITUDES_RE = re.compile(r"на\s+всех\s+высотах", re.IGNORECASE)
_RANGE_RE = re.compile(r"^\s*от\s+(?P<low>.+?)\s+до\s+(?P<high>.+?)\s*$", re.IGNORECASE | re.DOTALL)
_BAD_GEOMETRY_PREFIX = re.compile(r"^\s*некорректная\s+геометрия!\s*", re.IGNORECASE)


# --------------------------------------------------------------------------
# Разбор XML: KML читается без учёта пространства имён
# --------------------------------------------------------------------------


#: Кольцо координат KML: долгота, широта, высота.
_Ring = list[tuple[float, float, float]]


def _local(tag: str) -> str:
    """Имя элемента без пространства имён."""
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> Iterator[ET.Element]:
    """Прямые потомки с заданным именем."""
    for child in element:
        if _local(child.tag) == name:
            yield child


def _descendants(element: ET.Element, name: str) -> Iterator[ET.Element]:
    """Все потомки с заданным именем, на любой глубине."""
    for child in element.iter():
        if _local(child.tag) == name:
            yield child


def _text_of(element: ET.Element, name: str) -> str:
    """Текст первого потомка с заданным именем; пусто, если его нет."""
    for child in _descendants(element, name):
        return (child.text or "").strip()
    return ""


def _extended_data(placemark: ET.Element) -> dict[str, str]:
    """`ExtendedData` в виде плоского словаря.

    Понимаются обе формы: `<Data name="..."><value>` (так пишет заказчик и так
    же устроен предложенный нами формат) и `<SchemaData><SimpleData name="...">`.
    """
    result: dict[str, str] = {}
    for data in _descendants(placemark, "Data"):
        name = data.get("name")
        if name:
            result[name] = _text_of(data, "value")
    for simple in _descendants(placemark, "SimpleData"):
        name = simple.get("name")
        if name:
            result[name] = (simple.text or "").strip()
    return result


def _coordinates(element: ET.Element) -> list[tuple[float, float, float]]:
    """Содержимое `<coordinates>` в список троек.

    Терпит то, что встречается в выгрузках: пробелы после запятых, пропущенную
    высоту, лишние пустые поля. Неразбираемая тройка пропускается — решение о
    судьбе объекта принимает вызывающий, по числу уцелевших точек.
    """
    points: list[tuple[float, float, float]] = []
    for token in (element.text or "").split():
        parts = [part for part in token.split(",") if part.strip()]
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
            alt = float(parts[2]) if len(parts) > 2 else 0.0
        except ValueError:
            continue
        points.append((lon, lat, alt))
    return points


def _rings(geometry: ET.Element) -> tuple[_Ring, list[_Ring]]:
    """Внешнее кольцо и отверстия одного `<Polygon>`."""
    outer: _Ring = []
    holes: list[_Ring] = []
    for boundary in _children(geometry, "outerBoundaryIs"):
        for coords in _descendants(boundary, "coordinates"):
            outer = _coordinates(coords)
            break
    for boundary in _children(geometry, "innerBoundaryIs"):
        for coords in _descendants(boundary, "coordinates"):
            hole = _coordinates(coords)
            if hole:
                holes.append(hole)
            break
    return outer, holes


def _root(source: str | Path) -> ET.Element:
    """Корень документа KML.

    Raises:
        GeometryError: файл не читается или это не XML.
    """
    is_content = isinstance(source, str) and source.lstrip().startswith("<")
    try:
        if not is_content:
            return ET.parse(Path(source)).getroot()
        return ET.fromstring(str(source))
    except (OSError, ET.ParseError) as error:
        raise GeometryError(f"Файл KML не прочитан: {error}") from error


def _placemarks(source: str | Path) -> list[ET.Element]:
    return list(_descendants(_root(source), "Placemark"))


# --------------------------------------------------------------------------
# Отчёт импорта
# --------------------------------------------------------------------------


@dataclass
class _Log:
    """Накопитель предупреждений и счётчиков одного слоя."""

    stats: LayerStats = field(default_factory=LayerStats)
    warnings: list[PlanWarning] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def say(
        self, code: str, message: str, severity: Severity = Severity.WARNING, once: bool = False
    ) -> None:
        """Добавить предупреждение; `once` — не повторять одно и то же на файл."""
        if once:
            if code in self._seen:
                return
            self._seen.add(code)
        self.warnings.append(
            PlanWarning(code=code, severity=severity, stage=Stage.GEO, message=message)
        )


def _projector_for(points: list[GeoPoint]) -> Projector:
    crs, _ = pick_working_crs(points)
    return Projector(crs)


# --------------------------------------------------------------------------
# Слой 1: область съёмки
# --------------------------------------------------------------------------


def _repair(polygon: Polygon) -> tuple[Polygon | None, str]:
    """Починить полигон, если починка не меняет его по существу.

    Returns:
        Пара «полигон, что сделано». Полигон None — чинить нечего или нельзя,
        причина в строке. Строка пуста — полигон был корректен.
    """
    if polygon.is_empty or len(polygon.exterior.coords) < 4:
        return None, "кольцо меньше трёх различных точек"
    tolerance = assumptions.GEOMETRY_TOLERANCE_M**2
    if polygon.is_valid:
        if polygon.area <= tolerance:
            return None, "нулевая площадь"
        return polygon, ""
    fixed = polygon.buffer(0)
    if fixed.is_empty or fixed.area <= tolerance:
        return None, "нулевая площадь после починки"
    if not isinstance(fixed, Polygon):
        parts = sorted(fixed.geoms, key=lambda part: part.area, reverse=True)
        fixed = parts[0]
    raw = abs(polygon.area)
    change = abs(fixed.area - raw) / raw if raw > tolerance else 1.0
    if change > assumptions.IMPORT_REPAIR_AREA_FRACTION:
        return None, ""
    return fixed, f"исправлено, площадь изменилась на {abs(fixed.area - raw):.1f} м²"


def read_area(source: str | Path) -> tuple[GeoMultiPolygon, ImportReport]:
    """Прочитать слой области съёмки (docs/input_formats.md §2).

    Каждый `Placemark` даёт один кусок мультиполигона; `innerBoundaryIs` —
    отверстия куска. Высоты и `altitudeMode` игнорируются: область плоская,
    высоту съёмки считает модель (E2).

    Args:
        source: путь к файлу KML или его содержимое.

    Returns:
        Пара «область, отчёт импорта».

    Raises:
        GeometryError: не удалось прочитать файл; не осталось ни одного куска;
            кусок некорректен настолько, что починка изменила бы его по
            существу — с причиной и местом в WGS84.
    """
    log = _Log()
    raw: list[tuple[list[tuple[float, float, float]], list[list[tuple[float, float, float]]]]] = []
    for placemark in _placemarks(source):
        for geometry in _descendants(placemark, "Polygon"):
            outer, holes = _rings(geometry)
            log.stats.read += 1
            if len(outer) < 4:
                log.stats.dropped += 1
                log.say(
                    "import.geometry_dropped",
                    "Куски области с контуром меньше трёх точек выброшены "
                    "(сколько именно — в счётчике `dropped`)",
                    once=True,
                )
                continue
            raw.append((outer, holes))
    if not raw:
        raise GeometryError("В файле области съёмки нет ни одного полигона")

    anchor = [GeoPoint(lon=lon, lat=lat) for outer, _ in raw for lon, lat, _ in outer[:1]]
    projector = _projector_for(anchor)

    pieces: list[Polygon] = []
    for outer, holes in raw:
        shell = projector.ring_to_metric([GeoPoint(lon=lon, lat=lat) for lon, lat, _ in outer])
        rings = [
            projector.ring_to_metric([GeoPoint(lon=lon, lat=lat) for lon, lat, _ in hole])
            for hole in holes
        ]
        polygon = Polygon(shell, [ring for ring in rings if len(ring) >= 4])
        fixed, note = _repair(polygon)
        if fixed is None and not note:
            raise GeometryError(
                f"Кусок области: {projector.describe_invalid(polygon)}. "
                "Починка изменила бы площадь больше допустимого"
            )
        if fixed is None:
            log.stats.dropped += 1
            log.say(
                "import.geometry_dropped",
                "Вырожденные куски области выброшены: нулевая площадь в проекции "
                "(сколько именно — в счётчике `dropped`)",
                once=True,
            )
            continue
        if note:
            log.stats.fixed += 1
            log.say(
                "import.geometry_fixed",
                "Самопересечения в контурах исправлены; площадь каждого куска "
                f"изменилась не больше чем на {assumptions.IMPORT_REPAIR_AREA_FRACTION:.0%} "
                "(сколько именно — в счётчике `fixed`)",
                once=True,
            )
        pieces.append(fixed)
    if not pieces:
        raise GeometryError("В файле области съёмки не осталось ни одного пригодного куска")

    # Куски, у которых общая граница, обязаны стать одним: мультиполигон, части
    # которого делят ребро, некорректен по OGC, и `build_work_area` его отвергнет.
    parts = polygons_of(unary_union(pieces))
    if len(parts) < len(pieces):
        log.say(
            "import.geometry_merged",
            f"Соседние куски области слиты: {len(pieces)} записей файла дают "
            f"{len(parts)} раздельных кусков — у остальных общая граница",
        )
    log.stats.kept = len(parts)

    report = ImportReport(
        area=log.stats,
        area_km2=sum(part.area for part in parts) / 1e6,
        warnings=log.warnings,
    )
    return GeoMultiPolygon(polygons=[_polygon_to_geo(projector, part) for part in parts]), report


def _polygon_to_geo(projector: Projector, polygon: Polygon) -> GeoPolygon:
    """Полигон в метрах → полигон контракта в WGS84."""
    outer = [
        GeoPoint(lon=lon, lat=lat) for lon, lat in projector.coords_to_geo(polygon.exterior.coords)
    ]
    holes = [
        [GeoPoint(lon=lon, lat=lat) for lon, lat in projector.coords_to_geo(ring.coords)]
        for ring in polygon.interiors
    ]
    return GeoPolygon(outer=outer, holes=holes)


# --------------------------------------------------------------------------
# Слой 2: зоны ограничений
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AltitudeRange:
    """Высотный диапазон зоны, разобранный из текста заказчика."""

    min_m: float | None
    max_m: float | None
    datum: str
    parsed: bool


def _flight_level_m(level: int) -> float:
    """Эшелон FL в метры: FL*N* — это *N* × 100 футов по стандартной атмосфере."""
    return level * 100.0 * assumptions.FOOT_M


def _bound_m(token: str) -> float | None:
    """Одна граница диапазона: «земли», «N м», «FLK»."""
    if _GROUND_RE.search(token):
        return 0.0
    level = _FL_RE.search(token)
    if level:
        return _flight_level_m(int(level.group(1)))
    meters = _METERS_RE.search(token)
    if meters:
        return float(meters.group(1).replace(",", "."))
    return None


def parse_altitude_text(text: str) -> AltitudeRange:
    """Разобрать высоты зоны из текста заказчика (docs/input_formats.md §3.1).

    Понимает «На всех высотах», «От земли до N м (M фут) AGL|AMSL», «От земли
    до FLK», «От N м AMSL до FLK», «От FLK до FLL». Терпит префикс
    «Некорректная геометрия!», абзац-исключение после высот и опечатки
    распознавания вроде латинской `y` в слове «фyт».

    Args:
        text: значение поля `Altitudes` или `<description>`.

    Returns:
        Диапазон. `parsed = False` — текст не подошёл ни под один шаблон;
        такая зона считается действующей на всех высотах, а вызывающий
        обязан сказать об этом вслух.
    """
    head = _BAD_GEOMETRY_PREFIX.sub("", (text or "").strip()).split("\n")[0].strip()
    if not head:
        return AltitudeRange(None, None, "", False)
    if _ALL_ALTITUDES_RE.search(head):
        return AltitudeRange(None, None, "", True)
    datum = "AGL" if re.search(r"\bAGL\b", head, re.IGNORECASE) else ""
    if not datum and (re.search(r"\bAMSL\b", head, re.IGNORECASE) or _FL_RE.search(head)):
        datum = "AMSL"
    match = _RANGE_RE.match(head)
    if not match:
        return AltitudeRange(None, None, datum, False)
    low = _bound_m(match.group("low"))
    high = _bound_m(match.group("high"))
    if low is None or high is None or high <= low:
        return AltitudeRange(None, None, datum, False)
    return AltitudeRange(low, high, datum or "AGL", True)


def _to_agl(
    altitudes: AltitudeRange, terrain_min_m: float, terrain_max_m: float
) -> tuple[float | None, float | None]:
    """Перевести разобранный диапазон в высоты над поверхностью (№10).

    Обе поправки опускают границы зоны: верх — на минимальную отметку рельефа,
    низ — на максимальную. Зона от этого только расширяется вниз, то есть
    сторона ошибки безопасная.
    """
    if not altitudes.parsed or altitudes.min_m is None or altitudes.max_m is None:
        return None, None
    if altitudes.datum != "AMSL":
        return altitudes.min_m, altitudes.max_m
    low = max(0.0, altitudes.min_m - terrain_max_m)
    high = altitudes.max_m - terrain_min_m
    if high <= low:
        return None, None
    return low, high


def read_zones(
    source: str | Path,
    within: BaseGeometry | None = None,
    terrain_min_m: float = _NO_TERRAIN_M,
    terrain_max_m: float = _NO_TERRAIN_M,
) -> tuple[list[NoFlyZone], ImportReport]:
    """Прочитать слой зон ограничений (docs/input_formats.md §3).

    Читаются обе формы: свободный текст заказчика (`Name`, `Type`,
    `Altitudes`) и предложенный нами машинный формат (`zone_id`, `zone_kind`,
    `alt_min_m`, `alt_max_m`, `alt_datum`, `active_windows`). При наличии
    машинных полей верны они.

    Args:
        source: путь к файлу KML или его содержимое.
        within: область отбора в WGS84 (обычно bbox области съёмки с запасом).
            None — взять все зоны файла.
        terrain_min_m: минимальная отметка рельефа области, метры.
        terrain_max_m: максимальная отметка рельефа области, метры.

    Returns:
        Пара «зоны, отчёт импорта».
    """
    log = _Log()
    zones: list[NoFlyZone] = []
    for index, placemark in enumerate(_placemarks(source)):
        log.stats.read += 1
        data = _extended_data(placemark)
        polygons = [
            ring
            for geometry in _descendants(placemark, "Polygon")
            for ring in [_rings(geometry)[0]]
            if len(ring) >= 4
        ]
        if not polygons:
            log.stats.dropped += 1
            log.say("import.zone_dropped", "Зона без пригодного контура пропущена")
            continue
        shape = Polygon([(lon, lat) for lon, lat, _ in polygons[0]])
        if within is not None and not shape.intersects(within):
            log.stats.skipped += 1
            continue

        name = (
            data.get("zone_id")
            or data.get("Name")
            or _text_of(placemark, "name")
            or f"zone-{index}"
        )
        # Подпись зоны — `<name>`: у заказчика он совпадает с `Name`, в
        # предложенном формате идентификатор отдельно, в `zone_id`.
        title = _text_of(placemark, "name") or data.get("Name") or name
        windows = _windows(data.get("active_windows", ""), name, log)
        machine_kind = (data.get("zone_kind") or "").strip()
        kind_text = machine_kind or (data.get("Type") or "").strip()
        known_kinds = _MACHINE_ZONE_KINDS if machine_kind else _ZONE_KINDS
        kind, permanent = known_kinds.get(kind_text, ("restricted", True))
        if kind_text and kind_text not in known_kinds:
            log.say(
                "import.zone_kind_unknown",
                f"Зона {name}: неизвестный тип «{kind_text}» — считается постоянной",
                once=True,
            )
        if not permanent and not windows:
            log.say(
                "import.zone_window_unknown",
                f"Зона {name}: тип «{kind_text}», но времени действия в файле нет — "
                "зона считается действующей всегда. Формат окон — docs/input_formats.md §3.2",
                once=True,
            )

        machine = _machine_altitudes(data)
        if machine is not None:
            if not machine.parsed:
                log.say(
                    "import.zone_altitude_unparsed",
                    f"Зона {name}: высоты alt_min_m / alt_max_m не числа — "
                    "зона считается запретной на всех высотах",
                )
            min_alt, max_alt = _machine_to_agl(machine, terrain_min_m, terrain_max_m)
        else:
            text = data.get("Altitudes") or _text_of(placemark, "description")
            altitudes = parse_altitude_text(text)
            if not altitudes.parsed and text:
                log.say(
                    "import.zone_altitude_unparsed",
                    f"Зона {name}: высоты «{text.splitlines()[0][:80]}» не разобраны — "
                    "зона считается запретной на всех высотах",
                )
            min_alt, max_alt = _to_agl(altitudes, terrain_min_m, terrain_max_m)
            if altitudes.datum == "AMSL" and max_alt is not None and terrain_max_m == _NO_TERRAIN_M:
                log.say(
                    "import.zone_datum_unverified",
                    "Высоты зон заданы над уровнем моря, а рельеф не задан: "
                    "отметки прочитаны как высоты над поверхностью (ЦМР — E1, P1)",
                    once=True,
                )

        # Контракт хранит одно окно на зону (docs/input_formats.md §3.2): при
        # нескольких окнах зона разворачивается в записи с общим именем.
        spans: list[tuple[datetime | None, datetime | None]] = list(windows) or [(None, None)]
        for part, ring in enumerate(polygons):
            base_id = name if len(polygons) == 1 else f"{name}-{part + 1}"
            for number, (start, end) in enumerate(spans):
                zones.append(
                    NoFlyZone(
                        id=base_id if len(spans) == 1 else f"{base_id}-w{number + 1}",
                        name=title,
                        polygon=GeoPolygon(
                            outer=[GeoPoint(lon=lon, lat=lat) for lon, lat, _ in ring]
                        ),
                        min_alt_m=min_alt,
                        max_alt_m=max_alt,
                        active_from=start,
                        active_to=end,
                    )
                )
        log.stats.kept += 1
    return zones, ImportReport(zones=log.stats, warnings=log.warnings)


def _machine_altitudes(data: dict[str, str]) -> AltitudeRange | None:
    """Высоты из предложенного формата (§3.2); None — машинных полей в зоне нет.

    Пустой `alt_max_m` по формату значит «на всех высотах» выше нижней границы.
    Нечисловое значение — диапазон с `parsed = False`: вызывающий говорит об
    этом вслух и считает зону запретной на всех высотах.
    """
    if "alt_max_m" not in data and "alt_min_m" not in data:
        return None
    datum = (data.get("alt_datum") or "AGL").strip().upper()
    try:
        low = float(data.get("alt_min_m") or 0.0)
        high_text = (data.get("alt_max_m") or "").strip()
        high = float(high_text) if high_text else None
    except ValueError:
        return AltitudeRange(None, None, datum, False)
    if high is not None and high <= low:
        return AltitudeRange(None, None, datum, False)
    return AltitudeRange(low, high, datum, True)


def _machine_to_agl(
    altitudes: AltitudeRange, terrain_min_m: float, terrain_max_m: float
) -> tuple[float | None, float | None]:
    """Высоты предложенного формата в высоты над поверхностью.

    Верхняя граница есть — как у текста заказчика (`_to_agl`). Её нет — зона
    действует от нижней границы и выше; нижняя граница над уровнем моря
    опускается на максимальную отметку рельефа (сторона ошибки безопасная).
    Нижняя граница «от земли» записывается как отсутствующая, как в контракте.
    """
    if not altitudes.parsed or altitudes.min_m is None:
        return None, None
    if altitudes.max_m is not None:
        return _to_agl(altitudes, terrain_min_m, terrain_max_m)
    low = altitudes.min_m
    if altitudes.datum == "AMSL":
        low = max(0.0, low - terrain_max_m)
    return (low if low > 0.0 else None), None


def _windows(text: str, zone: str, log: _Log) -> list[tuple[datetime | None, datetime | None]]:
    """Окна действия зоны из `active_windows`: «начало/конец;начало/конец», UTC.

    Открытый конец интервала — «..» (ISO 8601-2): «2026-09-20T07:00:00Z/..» —
    зона действует с этого момента. Нечитаемое окно не выбрасывается молча:
    зона тогда считается действующей всегда — это безопасная сторона — и в
    отчёт идёт предупреждение.
    """

    def moment(value: str) -> datetime | None:
        value = value.strip()
        if value in ("", ".."):
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    windows: list[tuple[datetime | None, datetime | None]] = []
    for item in (part.strip() for part in text.split(";")):
        if not item:
            continue
        start_text, _, end_text = item.partition("/")
        try:
            start, end = moment(start_text), moment(end_text)
        except ValueError:
            start = end = None
        if start is None and end is None:
            log.say(
                "import.zone_window_unparsed",
                f"Зона {zone}: окно действия «{item}» не разобрано — "
                "зона считается действующей всегда",
            )
            return []
        if start is not None and end is not None and end <= start:
            log.say(
                "import.zone_window_unparsed",
                f"Зона {zone}: окно действия «{item}» пустое — зона считается действующей всегда",
            )
            return []
        windows.append((start, end))
    return windows


# --------------------------------------------------------------------------
# Слой 3: высотные препятствия
# --------------------------------------------------------------------------


def read_obstacles(
    source: str | Path,
    within: BaseGeometry | None = None,
    terrain_m: float = _NO_TERRAIN_M,
) -> tuple[list[Obstacle], ImportReport]:
    """Прочитать слой высотных препятствий (docs/input_formats.md §4).

    «3d примитив» заказчика — контур с высотой `Z` в координатах и
    `<extrude>1</extrude>`. Смысл `Z` задаёт `altitudeMode`: над землёй или
    над уровнем моря. Линейное препятствие (ЛЭП) разворачивается в полигон
    буфером, потому что контракт знает только точку и полигон.

    Args:
        source: путь к файлу KML или его содержимое.
        within: область отбора в WGS84. None — взять все препятствия файла.
        terrain_m: отметка рельефа для перевода абсолютных высот, метры.

    Returns:
        Пара «препятствия, отчёт импорта».
    """
    log = _Log()
    raw: list[tuple[str, str, str, list[tuple[float, float, float]], str]] = []
    for index, placemark in enumerate(_placemarks(source)):
        log.stats.read += 1
        name = _text_of(placemark, "name").strip()
        identifier, _, kind = name.partition(" ")
        identifier = identifier or f"obstacle-{index}"
        mode = _text_of(placemark, "altitudeMode").strip().lower()
        for geometry_name in ("Polygon", "LineString", "Point"):
            for geometry in _descendants(placemark, geometry_name):
                if geometry_name == "Polygon":
                    points, _ = _rings(geometry)
                else:
                    # `next(..., None)`, а не `any(...)`: элемент без потомков в
                    # ElementTree ложен, и проверка «есть ли координаты» через
                    # истинность выбрасывала бы все линейные препятствия.
                    coords = next(_descendants(geometry, "coordinates"), None)
                    points = _coordinates(coords) if coords is not None else []
                raw.append((identifier, kind.strip(), mode, points, geometry_name))

    # Записи без координат выбрасываются до выбора проекции: точек для неё нет.
    usable = []
    for item in raw:
        points = item[3]
        if not points or all(lon == 0.0 and lat == 0.0 for lon, lat, _ in points):
            log.stats.dropped += 1
            log.say(
                "import.obstacle_dropped",
                "Препятствие без координат или с координатами 0,0 выброшено",
                once=True,
            )
            continue
        usable.append(item)
    if not usable:
        return [], ImportReport(obstacles=log.stats, warnings=log.warnings)
    anchor = [GeoPoint(lon=points[0][0], lat=points[0][1]) for *_, points, _ in usable]
    projector = _projector_for(anchor)
    selection = within

    obstacles: list[Obstacle] = []
    seen: dict[str, int] = {}
    for identifier, kind, mode, points, geometry_name in usable:
        height = _obstacle_height(mode, points[0][2], terrain_m, log)
        if height is None:
            log.stats.dropped += 1
            continue
        is_line = geometry_name == "LineString"
        if geometry_name == "Point":
            lon, lat, _ = points[0]
            if selection is not None and not ShapelyPoint(lon, lat).intersects(selection):
                log.stats.skipped += 1
                continue
            count = seen.get(identifier, 0)
            seen[identifier] = count + 1
            # Мачта, труба — точка с высотой: так её и хранит контракт.
            obstacles.append(
                Obstacle(
                    id=identifier if count == 0 else f"{identifier}-{count + 1}",
                    name=kind,
                    position=GeoPoint(lon=lon, lat=lat),
                    height_m=height,
                )
            )
            log.stats.kept += 1
            continue
        shape = (
            LineString([(lon, lat) for lon, lat, _ in points])
            if is_line
            else Polygon([(lon, lat) for lon, lat, _ in points])
            if len(points) >= 4
            else LineString([(lon, lat) for lon, lat, _ in points])
        )
        if selection is not None and not shape.intersects(selection):
            log.stats.skipped += 1
            continue
        metric = projector.ring_to_metric([GeoPoint(lon=lon, lat=lat) for lon, lat, _ in points])
        valid_as_is = False
        if is_line or len(metric) < 4:
            footprint = LineString(metric).buffer(assumptions.OBSTACLE_LINE_WIDTH_M / 2.0)
            log.say(
                "import.obstacle_line_buffered",
                f"Линейные препятствия развёрнуты в полосу шириной "
                f"{assumptions.OBSTACLE_LINE_WIDTH_M:.0f} м (допущение команды)",
                once=True,
            )
        else:
            footprint = Polygon(metric)
            valid_as_is = footprint.is_valid and footprint.area > 0.0
            repaired, _ = _repair(footprint)
            if repaired is None:
                log.stats.dropped += 1
                log.say("import.obstacle_dropped", f"Препятствие {identifier}: вырожденный контур")
                continue
            footprint = repaired
        count = seen.get(identifier, 0)
        seen[identifier] = count + 1
        # Корректный контур переносится как есть: круг через метры и обратно
        # добавил бы в координаты шум, ничего не исправив.
        polygon = (
            GeoPolygon(outer=[GeoPoint(lon=lon, lat=lat) for lon, lat, _ in points])
            if valid_as_is
            else _polygon_to_geo(projector, footprint)
        )
        obstacles.append(
            Obstacle(
                id=identifier if count == 0 else f"{identifier}-{count + 1}",
                name=kind,
                polygon=polygon,
                height_m=height,
            )
        )
        log.stats.kept += 1
    return obstacles, ImportReport(obstacles=log.stats, warnings=log.warnings)


def _obstacle_height(mode: str, z: float, terrain_m: float, log: _Log) -> float | None:
    """Высота препятствия над землёй по `altitudeMode` и координате Z."""
    normalized = mode.strip()
    if normalized in _AGL_MODES:
        height = z
    elif normalized in _AMSL_MODES:
        if normalized in _NONSTANDARD_MODES:
            log.say(
                "import.obstacle_altitude_mode_nonstandard",
                f"Значение altitudeMode «{normalized}» не из словаря KML — прочитано как "
                "отметка над уровнем моря (docs/input_formats.md §4.2)",
                once=True,
            )
        height = z - terrain_m
    else:
        if not normalized:
            log.say(
                "import.obstacle_dropped",
                "Препятствие без altitudeMode выброшено: высота не определена",
                once=True,
            )
            return None
        log.say(
            "import.obstacle_altitude_mode_nonstandard",
            f"Значение altitudeMode «{mode}» не из словаря KML — прочитано как отметка "
            "над уровнем моря (docs/input_formats.md §4.2)",
            once=True,
        )
        height = z - terrain_m
    if height <= 0.0:
        return None
    return height


# --------------------------------------------------------------------------
# Три слоя разом
# --------------------------------------------------------------------------


@dataclass
class ImportedLayers:
    """Три входных слоя и общий отчёт импорта."""

    area: GeoMultiPolygon
    zones: list[NoFlyZone]
    obstacles: list[Obstacle]
    report: ImportReport


def import_layers(
    area_source: str | Path,
    zones_source: str | Path | None = None,
    obstacles_source: str | Path | None = None,
    terrain_min_m: float = _NO_TERRAIN_M,
    terrain_max_m: float = _NO_TERRAIN_M,
) -> ImportedLayers:
    """Прочитать три слоя и отобрать зоны и препятствия по области съёмки.

    Отбор — по описанному прямоугольнику области с запасом
    `IMPORT_SELECT_MARGIN_M`: файл зон заказчика покрывает всю европейскую
    часть страны, а область съёмки — один район.

    Args:
        area_source: файл области съёмки.
        zones_source: файл зон ограничений; None — зон нет.
        obstacles_source: файл высотных препятствий; None — препятствий нет.
        terrain_min_m: минимальная отметка рельефа области, метры.
        terrain_max_m: максимальная отметка рельефа области, метры.

    Returns:
        Слои и отчёт.

    Raises:
        GeometryError: область не прочитана (см. `read_area`).
    """
    area, report = read_area(area_source)
    selection = _selection_box(area)

    zones: list[Obstacle] | list[NoFlyZone] = []
    if zones_source is not None:
        zones, zones_report = read_zones(zones_source, selection, terrain_min_m, terrain_max_m)
        report = _merge(report, zones_report)
    obstacles: list[Obstacle] = []
    if obstacles_source is not None:
        obstacles, obstacles_report = read_obstacles(obstacles_source, selection, terrain_min_m)
        report = _merge(report, obstacles_report)
    return ImportedLayers(area=area, zones=list(zones), obstacles=obstacles, report=report)


def _selection_box(area: GeoMultiPolygon) -> BaseGeometry:
    """Описанный прямоугольник области с запасом, в градусах WGS84."""
    lons = [point.lon for polygon in area.polygons for point in polygon.outer]
    lats = [point.lat for polygon in area.polygons for point in polygon.outer]
    margin_deg = assumptions.IMPORT_SELECT_MARGIN_M / 111_000.0
    return box(
        min(lons) - margin_deg,
        min(lats) - margin_deg,
        max(lons) + margin_deg,
        max(lats) + margin_deg,
    )


def _merge(left: ImportReport, right: ImportReport) -> ImportReport:
    """Сложить отчёты разных слоёв в один."""
    return ImportReport(
        area=left.area if left.area.read else right.area,
        zones=left.zones if left.zones.read else right.zones,
        obstacles=left.obstacles if left.obstacles.read else right.obstacles,
        area_km2=left.area_km2 or right.area_km2,
        warnings=[*left.warnings, *right.warnings],
    )


__all__ = [
    "AltitudeRange",
    "ImportedLayers",
    "import_layers",
    "parse_altitude_text",
    "read_area",
    "read_obstacles",
    "read_zones",
]
