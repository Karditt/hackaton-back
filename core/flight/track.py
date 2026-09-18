"""4D-траектория вылета (x, y, высота, время) и поиск сближений.

Главный способ проверки решения заказчиком — краш-тест: «нет столкновений и
опасных сближений» (№4). Сближение — это когда два борта одновременно ближе
минимума разделения по горизонтали И ближе вертикального интервала по высоте.
Минимумы: самолёт ~300 м, мультиротор 50 м (№37); для пары берётся больший.

Наземные фазы (взлёт и посадка на площадке) в проверку не входят: площадка —
это участок земли неограниченной вместимости (№28), расстановку бортов на ней
обеспечивает расчёт. Набор и снижение над площадкой — входят.

Функции здесь — геометрия, а не планирование: их используют и расписание
(E14), и независимый валидатор (E7).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core import assumptions
from core.schemas import FlightPhase, Sortie

_GROUND_PHASES = (FlightPhase.TAKEOFF, FlightPhase.LANDING)


@dataclass(frozen=True)
class Track:
    """Траектория вылета в метрах и секундах от начала работ."""

    sortie_id: str
    vehicle_id: str
    separation_m: float
    """Минимум горизонтального разделения этого борта, метры."""
    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    airborne_from_s: float
    airborne_to_s: float
    bounds: tuple[float, float, float, float]
    """Габарит в плане (min_x, min_y, max_x, max_y), метры."""

    def shifted(self, delta_s: float) -> Track:
        """Та же траектория, сдвинутая во времени на `delta_s` секунд."""
        return Track(
            sortie_id=self.sortie_id,
            vehicle_id=self.vehicle_id,
            separation_m=self.separation_m,
            t=self.t + delta_s,
            x=self.x,
            y=self.y,
            z=self.z,
            airborne_from_s=self.airborne_from_s + delta_s,
            airborne_to_s=self.airborne_to_s + delta_s,
            bounds=self.bounds,
        )

    def position_at(self, times_s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Положение в моменты времени — линейная интерполяция между точками."""
        return (
            np.interp(times_s, self.t, self.x),
            np.interp(times_s, self.t, self.y),
            np.interp(times_s, self.t, self.z),
        )


def build_track(sortie: Sortie, is_fixed_wing: bool) -> Track:
    """Собрать траекторию вылета из точек маршрута.

    Args:
        sortie: вылет с временами точек (`Waypoint.time_s`).
        is_fixed_wing: самолётный тип — определяет минимум разделения.

    Returns:
        Траектория, пригодная для поиска сближений.
    """
    times: list[float] = []
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    airborne_from = sortie.start_time_s
    airborne_to = sortie.end_time_s()
    for leg in sortie.legs:
        if leg.phase is FlightPhase.TAKEOFF:
            airborne_from = leg.waypoints[-1].time_s
        if leg.phase is FlightPhase.LANDING:
            airborne_to = leg.waypoints[0].time_s
        for waypoint in leg.waypoints:
            if times and waypoint.time_s <= times[-1]:
                # Стык участков: та же точка в тот же момент. np.interp
                # требует строго возрастающего времени.
                xs[-1], ys[-1], zs[-1] = waypoint.x_m, waypoint.y_m, waypoint.alt_m
                continue
            times.append(waypoint.time_s)
            xs.append(waypoint.x_m)
            ys.append(waypoint.y_m)
            zs.append(waypoint.alt_m)
    x = np.asarray(xs)
    y = np.asarray(ys)
    return Track(
        sortie_id=sortie.id,
        vehicle_id=sortie.vehicle_id,
        separation_m=assumptions.separation_minimum_m(is_fixed_wing),
        t=np.asarray(times),
        x=x,
        y=y,
        z=np.asarray(zs),
        airborne_from_s=airborne_from,
        airborne_to_s=airborne_to,
        bounds=(float(x.min()), float(y.min()), float(x.max()), float(y.max())),
    )


@dataclass(frozen=True)
class Conflict:
    """Первое сближение двух бортов."""

    time_s: float
    horizontal_m: float
    vertical_m: float
    x_m: float
    y_m: float
    required_m: float


def first_conflict(a: Track, b: Track) -> Conflict | None:
    """Найти первое опасное сближение двух траекторий.

    Args:
        a: первая траектория.
        b: вторая траектория.

    Returns:
        Первое сближение либо None, если борта разведены всё время.
    """
    start = max(a.airborne_from_s, b.airborne_from_s)
    end = min(a.airborne_to_s, b.airborne_to_s)
    if end <= start:
        return None
    required = max(a.separation_m, b.separation_m)
    if (
        a.bounds[0] - required > b.bounds[2]
        or b.bounds[0] - required > a.bounds[2]
        or a.bounds[1] - required > b.bounds[3]
        or b.bounds[1] - required > a.bounds[3]
    ):
        return None

    times = np.arange(start, end, assumptions.SEPARATION_SAMPLE_STEP_S)
    times = np.append(times, end)
    ax, ay, az = a.position_at(times)
    bx, by, bz = b.position_at(times)
    horizontal = np.hypot(ax - bx, ay - by)
    vertical = np.abs(az - bz)
    hits = (horizontal < required) & (vertical < assumptions.VERTICAL_SEPARATION_M)
    if not hits.any():
        return None
    index = int(np.argmax(hits))
    return Conflict(
        time_s=float(times[index]),
        horizontal_m=float(horizontal[index]),
        vertical_m=float(vertical[index]),
        x_m=float((ax[index] + bx[index]) / 2.0),
        y_m=float((ay[index] + by[index]) / 2.0),
        required_m=required,
    )


__all__ = ["Conflict", "Track", "build_track", "first_conflict"]
