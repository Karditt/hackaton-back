"""Галсы подобласти для конкретного борта и защитные зоны манёвра.

ЗАГЛУШКА E3 в части выбора направления: берётся длинная сторона описанного
прямоугольника маски, одна на всю область. Настоящий E3 перебирает угол с
целевой функцией, учитывающей ветер (заказчик, №45) и число разворотов.
Геометрия честная: метры, обрезка по подобласти, змейка, шаг — свой у
каждого борта (№27).
"""

from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.geo.project import polygon_from_shapely, polygons_of
from core.schemas import MetricLineString, MetricPoint, MetricPolygon, Strip, SurveyParams, Vehicle

#: Запас, на который линия-резак выпускается за габариты подобласти, метры.
#: Ровно по габаритам резать нельзя: после поворота диагональ длиннее стороны.
_CUT_MARGIN_M = 10.0


def azimuth_from_vector(dx: float, dy: float) -> float:
    """Азимут вектора.

    Args:
        dx: приращение по X рабочей проекции, метры (на восток).
        dy: приращение по Y рабочей проекции, метры (на север).

    Returns:
        Азимут, градусы от севера по часовой стрелке, 0..360.
    """
    return math.degrees(math.atan2(dx, dy)) % 360.0


def dominant_azimuth_deg(geometry: BaseGeometry) -> float:
    """Направление длинной стороны минимального описанного прямоугольника.

    TODO(E3): заменить перебором по углу с целевой функцией «число
    разворотов + длина галсов + угол к ветру» (заказчик, №45).

    Args:
        geometry: рабочая маска в метрах.

    Returns:
        Азимут галсов, градусы 0..180.
    """
    rectangle = geometry.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)[:4]
    best_azimuth = 0.0
    best_length = -1.0
    for start, end in zip(coords, coords[1:] + coords[:1], strict=True):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length > best_length:
            best_length = length
            best_azimuth = azimuth_from_vector(dx, dy)
    return best_azimuth % 180.0


def _lines_from(geometry: BaseGeometry) -> list[LineString]:
    """Разложить результат пересечения в список ломаных."""
    if geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    return [geom for geom in getattr(geometry, "geoms", []) if isinstance(geom, LineString)]


def cover_cell(
    cell_id: str,
    cell: Polygon,
    params: SurveyParams,
    azimuth_deg: float,
) -> list[Strip]:
    """Покрыть подобласть галсами одного борта.

    Первая линия — на половину шага внутрь от края: крайняя полоса захвата
    накрывает границу подобласти, а у соседней подобласти — свою. На стыке
    двух подобластей расстояние между крайними галсами — полусумма шагов, и
    перекрытие не меньше поперечного (заказчик, №27: стыковка — постобработка).

    Args:
        cell_id: идентификатор подобласти.
        cell: полигон подобласти, метры.
        params: параметры съёмки борта: шаг, высота, путевая скорость.
        azimuth_deg: направление галсов, градусы.

    Returns:
        Галсы змейкой в порядке облёта.
    """
    math_angle_deg = 90.0 - azimuth_deg
    pivot = cell.centroid
    rotated = affinity.rotate(cell, -math_angle_deg, origin=pivot, use_radians=False)
    min_x, min_y, max_x, max_y = rotated.bounds
    spacing_m = max(params.strip_spacing_m, assumptions.GEOMETRY_TOLERANCE_M)

    offsets: list[float] = []
    y = min_y + spacing_m / 2.0
    while y < max_y:
        offsets.append(y)
        y += spacing_m
    if not offsets:
        offsets.append((min_y + max_y) / 2.0)

    strips: list[Strip] = []
    forward = True
    for index, offset_y in enumerate(offsets):
        cutter = LineString([(min_x - _CUT_MARGIN_M, offset_y), (max_x + _CUT_MARGIN_M, offset_y)])
        pieces = _lines_from(rotated.intersection(cutter))
        pieces.sort(key=lambda line: line.coords[0][0], reverse=not forward)
        for piece_index, piece in enumerate(pieces):
            if piece.length <= assumptions.GEOMETRY_TOLERANCE_M:
                continue
            restored = affinity.rotate(piece, math_angle_deg, origin=pivot, use_radians=False)
            coords = list(restored.coords)
            if not forward:
                coords.reverse()
            heading = azimuth_from_vector(
                coords[-1][0] - coords[0][0], coords[-1][1] - coords[0][1]
            )
            strips.append(
                Strip(
                    id=f"{cell_id}:{params.vehicle_id}:s{index:03d}-{piece_index}",
                    cell_id=cell_id,
                    vehicle_id=params.vehicle_id,
                    line=MetricLineString(points=[MetricPoint(x_m=x, y_m=y) for x, y in coords]),
                    length_m=restored.length,
                    altitude_agl_m=params.altitude_agl_m,
                    speed_mps=params.survey_speed_mps,
                    azimuth_deg=heading,
                )
            )
        forward = not forward
    return strips


def maneuver_zones(strips: list[Strip], vehicle: Vehicle) -> list[MetricPolygon]:
    """Защитные зоны манёвра на концах галсов (заказчик, №37, №38).

    Самолёт не разворачивается на месте: на концах галсов ему нужна зона, в
    которой он маневрирует так, как сам считает нужным. Зона — объединение
    кругов радиусом `MANEUVER_ZONE_RADIUS_FACTOR` × радиус разворота вокруг
    концов галсов. Геометрия петли не строится. У мультиротора зон нет.

    Args:
        strips: галсы подобласти.
        vehicle: борт.

    Returns:
        Полигоны зон манёвра, метры.
    """
    if not vehicle.is_fixed_wing() or vehicle.turn_radius_m <= 0.0 or not strips:
        return []
    radius_m = assumptions.MANEUVER_ZONE_RADIUS_FACTOR * vehicle.turn_radius_m
    discs = []
    for strip in strips:
        for point in (strip.start(), strip.end()):
            discs.append(Point(point.x_m, point.y_m).buffer(radius_m))
    return [polygon_from_shapely(part) for part in polygons_of(unary_union(discs))]


def covered_area_m2(strips: list[Strip], swath_width_m: float, region: BaseGeometry) -> float:
    """Площадь области, реально накрытая полосами захвата, м²."""
    if not strips:
        return 0.0
    bands = [
        LineString([(point.x_m, point.y_m) for point in strip.line.points]).buffer(
            swath_width_m / 2.0, cap_style=2
        )
        for strip in strips
    ]
    union = unary_union(bands)
    if not isinstance(union, (Polygon, MultiPolygon)):
        return 0.0
    return float(union.intersection(region).area)


__all__ = [
    "azimuth_from_vector",
    "cover_cell",
    "covered_area_m2",
    "dominant_azimuth_deg",
    "maneuver_zones",
]
