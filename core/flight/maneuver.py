"""Зоны манёвра самолёта во времени (E3 → E7, E14).

Петля разворота не строится — вместо неё заказчик разрешил защитную зону на
концах галсов, внутри которой самолёт разворачивается как считает нужным
(№38). Значит зона занята бортом **всё время съёмки своей подобласти**, а не
только в момент, когда через неё проходит линия маршрута: в траектории
разворот — это отрезок между концами соседних галсов, и он проходит далеко
не по всей зоне.

Отсюда правило: две зоны манёвра, занятые в одно время на близких высотах,
не должны сходиться ближе минимума разделения (№37) — даже если сами
траектории разведены. Иначе два самолёта в соседних подобластях разойдутся
на бумаге и встретятся на развороте (долг №27).

Модуль — геометрия, а не планирование: его используют и расписание (E14), и
независимый валидатор (E7), как и `core.flight.track`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from core import assumptions
from core.geo.project import polygon_to_shapely
from core.schemas import CoverageSet, Sortie


@dataclass(frozen=True)
class ManeuverWindow:
    """Зона манёвра одной подобласти: где, на какой высоте и когда занята."""

    sortie_id: str
    vehicle_id: str
    cell_id: str
    footprint: BaseGeometry
    """Объединение зон манёвра подобласти, метры."""
    altitude_m: float
    """Высота съёмки подобласти, м AGL — разворот идёт на ней же."""
    from_s: float
    to_s: float
    separation_m: float
    """Минимум горизонтального разделения этого борта, метры."""

    def shifted(self, delta_s: float) -> ManeuverWindow:
        """То же окно, сдвинутое во времени на `delta_s` секунд."""
        return replace(self, from_s=self.from_s + delta_s, to_s=self.to_s + delta_s)


def maneuver_windows(
    sortie: Sortie, coverage: CoverageSet | None, is_fixed_wing: bool
) -> list[ManeuverWindow]:
    """Собрать занятые этим вылетом зоны манёвра с их временем.

    Args:
        sortie: вылет с временами точек (`Waypoint.time_s`).
        coverage: покрытие подобластей; None — зон манёвра нет.
        is_fixed_wing: самолётный тип — определяет минимум разделения.

    Returns:
        Окна по подобластям вылета. У мультиротора зон манёвра нет, и список
        пуст.
    """
    if coverage is None:
        return []
    cells = {
        item.cell_id: item
        for item in coverage.cells
        if item.vehicle_id == sortie.vehicle_id and item.maneuver_zones
    }
    windows: list[ManeuverWindow] = []
    for cell_id in sortie.cell_ids:
        item = cells.get(cell_id)
        if item is None:
            continue
        times = [
            waypoint.time_s
            for leg in sortie.legs
            if leg.cell_id == cell_id
            for waypoint in leg.waypoints
        ]
        if not times:
            continue
        windows.append(
            ManeuverWindow(
                sortie_id=sortie.id,
                vehicle_id=sortie.vehicle_id,
                cell_id=cell_id,
                footprint=unary_union([polygon_to_shapely(zone) for zone in item.maneuver_zones]),
                altitude_m=sortie.survey_altitude_m,
                from_s=min(times),
                to_s=max(times),
                separation_m=assumptions.separation_minimum_m(is_fixed_wing),
            )
        )
    return windows


@dataclass(frozen=True)
class ManeuverOverlap:
    """Опасное соседство двух зон манёвра."""

    first: ManeuverWindow
    second: ManeuverWindow
    from_s: float
    to_s: float
    distance_m: float
    vertical_m: float
    required_m: float
    x_m: float
    y_m: float


def maneuver_overlap(first: ManeuverWindow, second: ManeuverWindow) -> ManeuverOverlap | None:
    """Проверить пару зон манёвра на опасное соседство.

    Опасно, когда зоны заняты в общее время, разведены по высоте меньше чем
    на вертикальный интервал и сходятся ближе минимума разделения.

    Args:
        first: первая зона манёвра.
        second: вторая зона манёвра.

    Returns:
        Описание соседства либо None, если пара безопасна.
    """
    start = max(first.from_s, second.from_s)
    end = min(first.to_s, second.to_s)
    if end <= start:
        return None
    vertical = abs(first.altitude_m - second.altitude_m)
    if vertical >= assumptions.VERTICAL_SEPARATION_M:
        return None
    required = max(first.separation_m, second.separation_m)
    distance = first.footprint.distance(second.footprint)
    if distance >= required:
        return None
    # Место нарушения — середина кратчайшего отрезка между зонами.
    near_first, near_second = _closest_points(first.footprint, second.footprint)
    return ManeuverOverlap(
        first=first,
        second=second,
        from_s=start,
        to_s=end,
        distance_m=float(distance),
        vertical_m=float(vertical),
        required_m=required,
        x_m=(near_first[0] + near_second[0]) / 2.0,
        y_m=(near_first[1] + near_second[1]) / 2.0,
    )


def _closest_points(
    first: BaseGeometry, second: BaseGeometry
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Ближайшие точки двух геометрий; при пересечении — общая точка."""
    if first.intersects(second):
        point = first.intersection(second).representative_point()
        return (point.x, point.y), (point.x, point.y)
    near_first, near_second = nearest_points(first, second)
    return (near_first.x, near_first.y), (near_second.x, near_second.y)


__all__ = ["ManeuverOverlap", "ManeuverWindow", "maneuver_overlap", "maneuver_windows"]
