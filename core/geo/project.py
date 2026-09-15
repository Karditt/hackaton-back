"""Перевод координат между WGS84 и рабочей метрической проекцией.

Это физическая граница инварианта 1. Всё, что вошло сюда в градусах, выходит
в метрах и дальше по конвейеру живёт только в метрах; обратный перевод
вызывается ровно один раз — в `core.export`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from core import assumptions
from core.errors import GeometryError
from core.schemas import (
    CrsInfo,
    GeoPoint,
    GeoPolygon,
    MetricLineString,
    MetricPoint,
)


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

    def polygon_to_metric(self, polygon: GeoPolygon) -> Polygon:
        """Полигон WGS84 → полигон shapely в метрах.

        Args:
            polygon: полигон в градусах WGS84, кольца могут быть незамкнуты.

        Returns:
            `shapely.Polygon` в метрах рабочей проекции.

        Raises:
            GeometryError: полигон вырожден или имеет нулевую площадь.
        """
        shell = self.ring_to_metric(polygon.outer)
        holes = [self.ring_to_metric(hole) for hole in polygon.holes]
        result = Polygon(shell, holes)
        if result.is_empty or result.area <= assumptions.GEOMETRY_TOLERANCE_M**2:
            raise GeometryError("Полигон вырожден: нулевая площадь после проецирования")
        # TODO(E1): самопересечения чинятся buffer(0) молча. Правильнее —
        # разобрать случай и вернуть внятное предупреждение пользователю.
        if not result.is_valid:
            repaired = result.buffer(0)
            if repaired.is_empty:
                raise GeometryError("Полигон самопересекается и не поддаётся исправлению")
            return (
                repaired
                if isinstance(repaired, Polygon)
                else max(repaired.geoms, key=lambda geom: geom.area)
            )
        return result

    # ---------------- метры → WGS84 ----------------

    def to_geo(self, point: MetricPoint, alt_m: float | None = None) -> GeoPoint:
        """Точка рабочей проекции → точка WGS84.

        Args:
            point: точка в метрах рабочей проекции.
            alt_m: высота, метры. Смысл задаёт `Mission.altitude_mode`.

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


def geometry_area_m2(geometry: BaseGeometry) -> float:
    """Площадь геометрии, м². Проекция метрическая, поэтому площадь настоящая."""
    return float(geometry.area)


__all__ = [
    "Projector",
    "geometry_area_m2",
    "line_from_shapely",
    "line_to_shapely",
]
