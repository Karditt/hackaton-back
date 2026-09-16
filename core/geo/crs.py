"""Выбор рабочей метрической проекции под конкретный сценарий.

Проекция выбирается один раз на сценарий и дальше сопровождает каждую стадию
конвейера через `CrsInfo`. Метры без указания проекции необратимы обратно в
координаты, поэтому `CrsInfo` — обязательное поле всех промежуточных моделей.
"""

from __future__ import annotations

from collections.abc import Iterable

from core import assumptions
from core.errors import GeometryError
from core.schemas import CrsInfo, GeoPoint

#: Ширина зоны UTM, градусы долготы.
_UTM_ZONE_WIDTH_DEG = 6.0

#: Зон UTM ровно 60, нумерация с 1 от 180° з.д.
_UTM_ZONE_COUNT = 60


def utm_zone(lon: float) -> int:
    """Номер зоны UTM по долготе.

    Args:
        lon: долгота, градусы WGS84.

    Returns:
        Номер зоны, 1..60.
    """
    zone = int((lon + 180.0) // _UTM_ZONE_WIDTH_DEG) + 1
    return min(max(zone, 1), _UTM_ZONE_COUNT)


def utm_epsg(lon: float, lat: float) -> int:
    """Код EPSG зоны UTM, накрывающей точку.

    Args:
        lon: долгота, градусы WGS84.
        lat: широта, градусы WGS84.

    Returns:
        Код EPSG: 326NN для северного полушария, 327NN для южного.
    """
    base = assumptions.UTM_NORTH_EPSG_BASE if lat >= 0.0 else assumptions.UTM_SOUTH_EPSG_BASE
    return base + utm_zone(lon)


def centroid(points: Iterable[GeoPoint]) -> GeoPoint:
    """Среднее арифметическое координат — точка привязки проекции.

    Для областей размером в десятки километров этого достаточно: точка нужна
    лишь чтобы выбрать зону UTM, а не чтобы считать по ней расстояния.

    Args:
        points: точки в WGS84.

    Returns:
        Точка в WGS84.

    Raises:
        GeometryError: пустая последовательность точек.
    """
    items = list(points)
    if not items:
        raise GeometryError("Невозможно выбрать проекцию: не передано ни одной точки")
    return GeoPoint(
        lon=sum(point.lon for point in items) / len(items),
        lat=sum(point.lat for point in items) / len(items),
    )


def pick_working_crs(points: Iterable[GeoPoint]) -> tuple[CrsInfo, GeoPoint]:
    """Подобрать метрическую проекцию под набор точек сценария.

    Берётся зона UTM по центру области. Это компромисс: искажения внутри зоны
    не превышают долей процента, а сама проекция стандартна и читается любым
    ГИС-пакетом, куда пойдёт экспорт.

    TODO(E1): область, пересекающая границу зон UTM, получает искажения на
    дальнем краю. Разумный ответ — локальная поперечная проекция Меркатора
    с осевым меридианом по центру области. Сейчас не реализовано.

    Args:
        points: все опорные точки сценария (контур области, площадки,
            запретные зоны) в WGS84.

    Returns:
        Пара «описание проекции, точка привязки (центр области)».
    """
    origin = centroid(points)
    epsg = utm_epsg(origin.lon, origin.lat)
    hemisphere = "N" if origin.lat >= 0.0 else "S"
    return (
        CrsInfo(epsg=epsg, name=f"WGS84 / UTM zone {utm_zone(origin.lon)}{hemisphere}"),
        origin,
    )


__all__ = ["centroid", "pick_working_crs", "utm_epsg", "utm_zone"]
