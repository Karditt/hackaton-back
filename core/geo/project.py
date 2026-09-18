"""Перевод координат между WGS84 и рабочей метрической проекцией.

Это физическая граница инварианта 1. Всё, что вошло сюда в градусах, выходит
в метрах и дальше по конвейеру живёт только в метрах; обратный перевод
вызывается в `core.export` (и для текста ошибок, указывающих место).

Некорректная геометрия НЕ чинится молча: самопересечение — ошибка входа с
указанием места (заказчик, №9, №19). Исправлять её — дело ГИС, в которой
готовились данные (№8).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from core import assumptions
from core.errors import GeometryError
from core.schemas import (
    CrsInfo,
    GeoMultiPolygon,
    GeoPoint,
    GeoPolygon,
    MetricLineString,
    MetricPoint,
    MetricPolygon,
)

#: Место дефекта в тексте shapely: «Self-intersection[123.4 567.8]».
_LOCATION_RE = re.compile(r"\[([-+\d.eE]+)\s+([-+\d.eE]+)\]")

#: Перевод диагностик GEOS на язык пользователя.
_REASONS_RU = {
    "Self-intersection": "самопересечение контура",
    "Ring Self-intersection": "контур касается сам себя",
    "Interior is disconnected": "отверстия разрезают полигон на части",
    "Hole lies outside shell": "отверстие лежит вне внешнего контура",
    "Nested holes": "отверстие внутри другого отверстия",
    "Nested shells": "части мультиполигона вложены друг в друга",
    "Duplicate Rings": "повторяющиеся кольца",
    "Too few points": "слишком мало точек",
    "Invalid Coordinate": "некорректная координата",
}


class Projector:
    """Двусторонний преобразователь координат под одну рабочую проекцию.

    Трансформеры pyproj создаются один раз и переиспользуются: их построение
    заметно дороже самого преобразования.
    """

    def __init__(self, crs: CrsInfo) -> None:
        self.crs = crs
        source = CRS.from_epsg(assumptions.WGS84_EPSG)
        target = CRS.from_epsg(crs.epsg)
        if not target.is_projected:
            raise GeometryError(
                f"EPSG:{crs.epsg} не является проекционной системой — "
                "расчёты в градусах запрещены инвариантом 1"
            )
        self._forward = Transformer.from_crs(source, target, always_xy=True)
        self._inverse = Transformer.from_crs(target, source, always_xy=True)

    # ---------------- WGS84 → метры ----------------

    def to_metric(self, point: GeoPoint) -> MetricPoint:
        """Точка WGS84 → точка рабочей проекции.

        Args:
            point: точка в градусах WGS84.

        Returns:
            Точка в метрах рабочей проекции.
        """
        x_m, y_m = self._forward.transform(point.lon, point.lat)
        return MetricPoint(x_m=x_m, y_m=y_m)

    def ring_to_metric(self, ring: Sequence[GeoPoint]) -> list[tuple[float, float]]:
        """Кольцо WGS84 → список пар (x, y) в метрах."""
        lons = [point.lon for point in ring]
        lats = [point.lat for point in ring]
        xs, ys = self._forward.transform(lons, lats)
        return list(zip(xs, ys, strict=True))

    def polygon_to_metric(self, polygon: GeoPolygon, what: str = "Полигон") -> Polygon:
        """Полигон WGS84 → полигон shapely в метрах.

        Args:
            polygon: полигон в градусах WGS84, кольца могут быть незамкнуты.
            what: что это за полигон — для текста ошибки.

        Returns:
            `shapely.Polygon` в метрах рабочей проекции.

        Raises:
            GeometryError: полигон вырожден или некорректен (самопересечение и
                т.п.). Текст называет причину и место в WGS84.
        """
        shell = self.ring_to_metric(polygon.outer)
        holes = [self.ring_to_metric(hole) for hole in polygon.holes]
        result = Polygon(shell, holes)
        if result.is_empty or result.area <= assumptions.GEOMETRY_TOLERANCE_M**2:
            raise GeometryError(f"{what}: вырожден — нулевая площадь")
        if not result.is_valid:
            raise GeometryError(f"{what}: {self.describe_invalid(result)}")
        return result

    def multipolygon_to_metric(
        self, multipolygon: GeoMultiPolygon, what: str = "Мультиполигон"
    ) -> BaseGeometry:
        """Мультиполигон WGS84 → полигон или мультиполигон shapely в метрах.

        Args:
            multipolygon: части в градусах WGS84.
            what: что это за геометрия — для текста ошибки.

        Returns:
            `Polygon`, если часть одна, иначе `MultiPolygon`, метры.

        Raises:
            GeometryError: часть некорректна или части перекрываются.
        """
        parts = [
            self.polygon_to_metric(polygon, f"{what}, часть {index + 1}")
            for index, polygon in enumerate(multipolygon.polygons)
        ]
        if len(parts) == 1:
            return parts[0]
        result = MultiPolygon(parts)
        if not result.is_valid:
            raise GeometryError(f"{what}: {self.describe_invalid(result)}")
        return result

    def describe_invalid(self, geometry: BaseGeometry) -> str:
        """Причина некорректности на языке пользователя и её место в WGS84."""
        text = explain_validity(geometry)
        reason = text.split("[", 1)[0].strip()
        reason_ru = _REASONS_RU.get(reason, reason)
        match = _LOCATION_RE.search(text)
        if not match:
            return f"некорректная геометрия — {reason_ru}"
        place = self.xy_to_geo(float(match.group(1)), float(match.group(2)))
        return (
            f"некорректная геометрия — {reason_ru} в точке "
            f"({place.lon:.6f}, {place.lat:.6f}). Исправьте исходный слой в ГИС"
        )

    # ---------------- метры → WGS84 ----------------

    def to_geo(self, point: MetricPoint, alt_m: float | None = None) -> GeoPoint:
        """Точка рабочей проекции → точка WGS84.

        Args:
            point: точка в метрах рабочей проекции.
            alt_m: высота над поверхностью, метры.

        Returns:
            Точка в градусах WGS84.
        """
        lon, lat = self._inverse.transform(point.x_m, point.y_m)
        return GeoPoint(lon=lon, lat=lat, alt_m=alt_m)

    def xy_to_geo(self, x_m: float, y_m: float, alt_m: float | None = None) -> GeoPoint:
        """То же, что `to_geo`, но по голым координатам."""
        lon, lat = self._inverse.transform(x_m, y_m)
        return GeoPoint(lon=lon, lat=lat, alt_m=alt_m)

    def coords_to_geo(self, coords: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
        """Последовательность (x, y) в метрах → список (lon, lat) в градусах."""
        items = list(coords)
        if not items:
            return []
        xs = [item[0] for item in items]
        ys = [item[1] for item in items]
        lons, lats = self._inverse.transform(xs, ys)
        return list(zip(lons, lats, strict=True))


# ---------------- мосты между схемами и shapely ----------------


def line_to_shapely(line: MetricLineString) -> LineString:
    """Ломаная контракта → `shapely.LineString` (метры)."""
    return LineString([(point.x_m, point.y_m) for point in line.points])


def line_from_shapely(line: LineString) -> MetricLineString:
    """`shapely.LineString` (метры) → ломаная контракта."""
    return MetricLineString(points=[MetricPoint(x_m=x, y_m=y) for x, y in line.coords])


def polygon_to_shapely(polygon: MetricPolygon) -> Polygon:
    """Полигон контракта → `shapely.Polygon` (метры)."""
    return Polygon(
        [(point.x_m, point.y_m) for point in polygon.outer],
        [[(point.x_m, point.y_m) for point in hole] for hole in polygon.holes],
    )


def polygon_from_shapely(polygon: Polygon) -> MetricPolygon:
    """`shapely.Polygon` (метры) → полигон контракта. Замыкающая точка отбрасывается."""
    return MetricPolygon(
        outer=[MetricPoint(x_m=x, y_m=y) for x, y in list(polygon.exterior.coords)[:-1]],
        holes=[
            [MetricPoint(x_m=x, y_m=y) for x, y in list(ring.coords)[:-1]]
            for ring in polygon.interiors
        ],
    )


def polygons_of(geometry: BaseGeometry) -> list[Polygon]:
    """Полигональные части геометрии; обрывки линий и точки отбрасываются."""
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    return [part for part in getattr(geometry, "geoms", []) if isinstance(part, Polygon)]


def point_to_shapely(point: MetricPoint) -> Point:
    """Точка контракта → `shapely.Point` (метры)."""
    return Point(point.x_m, point.y_m)


def geometry_area_m2(geometry: BaseGeometry) -> float:
    """Площадь геометрии, м². Проекция метрическая, поэтому площадь настоящая."""
    return float(geometry.area)


__all__ = [
    "Projector",
    "geometry_area_m2",
    "line_from_shapely",
    "line_to_shapely",
    "point_to_shapely",
    "polygon_from_shapely",
    "polygon_to_shapely",
    "polygons_of",
]
