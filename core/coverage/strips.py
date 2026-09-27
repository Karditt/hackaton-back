"""Галсы подобласти для конкретного борта и защитные зоны манёвра.

Геометрия: метры, обрезка по подобласти, змейка, шаг — свой у каждого борта
(№27). Направление галсов выбирается на подобласть и борт (E3, как у
GeoScan Planner — docs/planner_reference.md §4): `candidate_azimuths` даёт
кандидатов в порядке числа разворотов, покрытие (`CoverageOracle`) берёт
первого, при котором зона манёвра самолёта не задевает запрещённого.

Ветер в выбор направления не входит: путевая скорость на галсе рассчитана так,
что удерживается на любом курсе (E2), и время от направления не зависит
(№45). Выгода галсов поперёк ветра — более высокая путевая скорость — требует
скорости по курсу, это долг №52.
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.geo.project import polygon_from_shapely, polygons_of
from core.schemas import MetricLineString, MetricPoint, MetricPolygon, Strip, SurveyParams, Vehicle

#: Запас, на который линия-резак выпускается за габариты подобласти, метры:
#: резак ровно по крайним вершинам касался бы контура, а не пересекал его.
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

    Args:
        geometry: геометрия в метрах.

    Returns:
        Азимут, градусы 0..180.
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


def _width_across(hull: np.ndarray, azimuth_deg: float) -> float:
    """Ширина выпуклой оболочки поперёк галсов данного направления, метры."""
    radians = math.radians(azimuth_deg)
    # Нормаль к направлению галсов (азимут от севера по часовой стрелке).
    normal = np.array([math.cos(radians), -math.sin(radians)])
    projection = hull @ normal
    return float(projection.max() - projection.min())


def candidate_azimuths(cell: Polygon, spacing_m: float) -> list[float]:
    """Кандидаты направления галсов подобласти, лучшие — первыми.

    Кандидаты: длинная сторона описанного прямоугольника, направления рёбер
    подобласти не короче шага галсов (вдоль ребра у границы запретной зоны
    крайний галс идёт параллельно ей, и разворот не смотрит в зону) и перебор
    с шагом `AZIMUTH_SEARCH_STEP_DEG`. Порядок — по числу галсов: ширина
    подобласти поперёк направления, делённая на шаг. Длина галсов в сумме от
    направления почти не зависит (площадь, делённая на шаг), а каждый лишний
    галс — лишний разворот.

    Args:
        cell: полигон подобласти, метры.
        spacing_m: шаг галсов борта, метры.

    Returns:
        Азимуты, градусы 0..180, без повторов; при равном числе галсов —
        в порядке перечисления выше.
    """
    candidates = [dominant_azimuth_deg(cell)]
    coords = np.asarray(cell.exterior.coords)
    edges = np.diff(coords, axis=0)
    lengths = np.hypot(edges[:, 0], edges[:, 1])
    for index in np.argsort(-lengths, kind="stable"):
        if lengths[index] < spacing_m:
            break
        candidates.append(azimuth_from_vector(*edges[index]) % 180.0)
    step = assumptions.AZIMUTH_SEARCH_STEP_DEG
    candidates.extend(float(value) for value in np.arange(0.0, 180.0, step))

    unique = list(dict.fromkeys(round(azimuth % 180.0, 6) for azimuth in candidates))
    hull = np.asarray(cell.convex_hull.exterior.coords)
    spacing = max(spacing_m, assumptions.GEOMETRY_TOLERANCE_M)
    strips = [math.ceil(_width_across(hull, azimuth) / spacing) for azimuth in unique]
    order = sorted(range(len(unique)), key=lambda index: strips[index])
    return [unique[index] for index in order]


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
    # Оси галсов: вдоль — направление азимута, поперёк — влево от него.
    # Поворачиваются линии-резаки, а не подобласть: повёрнутый полигон с узким
    # выступом из-за округления становится самопересекающимся, и пересечение
    # с ним даёт куски вне подобласти (так было на данных заказчика).
    radians = math.radians(azimuth_deg)
    along = (math.sin(radians), math.cos(radians))
    across = (-math.cos(radians), math.sin(radians))
    ring = np.asarray(cell.exterior.coords)
    x_along = ring @ np.array(along)
    y_across = ring @ np.array(across)
    min_x, max_x = float(x_along.min()), float(x_along.max())
    min_y, max_y = float(y_across.min()), float(y_across.max())
    spacing_m = max(params.strip_spacing_m, assumptions.GEOMETRY_TOLERANCE_M)

    def point(x: float, y: float) -> tuple[float, float]:
        return (x * along[0] + y * across[0], x * along[1] + y * across[1])

    def position(xy: tuple[float, ...]) -> float:
        return xy[0] * along[0] + xy[1] * along[1]

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
        cutter = LineString(
            [point(min_x - _CUT_MARGIN_M, offset_y), point(max_x + _CUT_MARGIN_M, offset_y)]
        )
        pieces = []
        for piece in _lines_from(cell.intersection(cutter)):
            coords = list(piece.coords)
            if position(coords[-1]) < position(coords[0]):
                coords.reverse()
            pieces.append((position(coords[0]), coords, piece.length))
        pieces.sort(key=lambda item: item[0], reverse=not forward)
        for piece_index, (_start, coords, length_m) in enumerate(pieces):
            if length_m <= assumptions.GEOMETRY_TOLERANCE_M:
                continue
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
                    length_m=length_m,
                    altitude_agl_m=params.altitude_agl_m,
                    speed_mps=params.survey_speed_mps,
                    azimuth_deg=heading,
                )
            )
        forward = not forward
    return strips


def _turn_zone(
    point: MetricPoint,
    along: tuple[float, float],
    partner: MetricPoint | None,
    radius_m: float,
    turn_radius_m: float,
) -> BaseGeometry:
    """Зона манёвра у одного конца галса, метры.

    Круг радиусом `radius_m` вокруг конца галса. Если известно, к какому
    соседнему галсу идёт разворот, круг срезается поперёк: петля минимального
    радиуса между параллельными линиями на расстоянии d выходит за каждую из
    них на R − d/2 (при d ≥ 2R — не выходит), и зона — полоса от своего галса
    минус эта выпуклость до соседнего плюс она. Сосед на той же линии (кусок
    галса за отверстием) или его нет — полный круг: куда разворачиваться, не
    известно.

    Args:
        point: конец галса.
        along: единичный вектор направления галса.
        partner: точка соседнего галса, к которому или от которого разворот.
        radius_m: радиус защитной зоны, метры.
        turn_radius_m: радиус разворота борта, метры.
    """
    disc = Point(point.x_m, point.y_m).buffer(radius_m)
    if partner is None:
        return disc
    normal = (-along[1], along[0])
    lateral = (partner.x_m - point.x_m) * normal[0] + (partner.y_m - point.y_m) * normal[1]
    if abs(lateral) <= assumptions.GEOMETRY_TOLERANCE_M:
        return disc
    side = (normal[0] * math.copysign(1.0, lateral), normal[1] * math.copysign(1.0, lateral))
    bulge_m = max(turn_radius_m - abs(lateral) / 2.0, 0.0)
    # Петля между соседними галсами лежит в полосе от своего галса минус
    # выпуклость до соседнего плюс выпуклость; вдоль — ±radius.
    far_m = min(radius_m, abs(lateral) + bulge_m)
    corners = [
        (
            point.x_m + side[0] * across + along[0] * length,
            point.y_m + side[1] * across + along[1] * length,
        )
        for across, length in (
            (-bulge_m, -radius_m),
            (-bulge_m, radius_m),
            (far_m, radius_m),
            (far_m, -radius_m),
        )
    ]
    return disc.intersection(Polygon(corners))


def maneuver_zones(strips: list[Strip], vehicle: Vehicle) -> list[MetricPolygon]:
    """Защитные зоны манёвра на концах галсов (заказчик, №37, №38).

    Самолёт не разворачивается на месте: на концах галсов ему нужна зона, в
    которой он маневрирует так, как сам считает нужным. Геометрия петли не
    строится. Зона у конца галса — круг радиусом `MANEUVER_ZONE_RADIUS_FACTOR`
    × радиус разворота, срезанный с внешней стороны: разворот идёт к соседнему
    галсу змейки (`_turn_zone`). У начала первого и конца последнего галса сосед
    — второй и предпоследний галсы: заход и выход тоже со стороны подобласти.
    У мультиротора зон нет.

    Срез — то, что делает самолёту доступной подобласть вдоль запретной зоны:
    крайний галс параллелен её границе, и полный круг вокруг его концов задевал
    бы зону при любом направлении галсов (долг №51).

    Args:
        strips: галсы подобласти в порядке облёта.
        vehicle: борт.

    Returns:
        Полигоны зон манёвра, метры.
    """
    if not vehicle.is_fixed_wing() or vehicle.turn_radius_m <= 0.0 or not strips:
        return []
    radius_m = assumptions.MANEUVER_ZONE_RADIUS_FACTOR * vehicle.turn_radius_m
    zones = []
    last = len(strips) - 1
    for index, strip in enumerate(strips):
        start, end = strip.start(), strip.end()
        length = math.hypot(end.x_m - start.x_m, end.y_m - start.y_m)
        if length <= assumptions.GEOMETRY_TOLERANCE_M:
            zones.append(Point(start.x_m, start.y_m).buffer(radius_m))
            continue
        along = ((end.x_m - start.x_m) / length, (end.y_m - start.y_m) / length)
        before = strips[index - 1].end() if index > 0 else None
        after = strips[index + 1].start() if index < last else None
        start_partner = before if before is not None else after
        end_partner = after if after is not None else before
        zones.append(_turn_zone(start, along, start_partner, radius_m, vehicle.turn_radius_m))
        zones.append(_turn_zone(end, along, end_partner, radius_m, vehicle.turn_radius_m))
    return [polygon_from_shapely(part) for part in polygons_of(unary_union(zones))]


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
    "candidate_azimuths",
    "cover_cell",
    "covered_area_m2",
    "dominant_azimuth_deg",
    "maneuver_zones",
]
