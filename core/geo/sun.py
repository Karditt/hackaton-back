"""Высота Солнца и окна съёмки по ней (E14, долг №55).

Аэрофотосъёмка ведётся при Солнце не ниже нормы: иначе длинные тени и
недосвет (РАФ-89 п. 21, ГКИНП-09-32-80: не ниже 20° на черно-белую, не ниже
25° на цветную). Заказчик время суток считать не требует, но учёт с сезоном
назвал плюсом (№22) — поэтому ограничение включается в сценарии
(`Scenario.min_sun_elevation_deg`) и по умолчанию выключено.

Положение Солнца — алгоритм NOAA Solar Calculator (по Меёсу, «Astronomical
Algorithms», 1991): точность высоты — сотые доли градуса в годы 1800–2100.
Коэффициенты ниже — часть формулы, а не допущения. Рефракция не учитывается:
у горизонта она поднимает Солнце на полградуса, а при высотах от 20° —
меньше чем на 0,05°.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from core import assumptions

_J2000_JD = 2451545.0
_UNIX_EPOCH_JD = 2440587.5
_SECONDS_PER_DAY = 86400.0


def as_utc(moment: datetime) -> datetime:
    """Момент времени с часовым поясом; время без пояса считается UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _julian_century(moment: datetime) -> float:
    """Юлианские столетия от эпохи J2000 для момента UTC."""
    julian_day = as_utc(moment).timestamp() / _SECONDS_PER_DAY + _UNIX_EPOCH_JD
    return (julian_day - _J2000_JD) / 36525.0


def sun_elevation_deg(moment: datetime, lat_deg: float, lon_deg: float) -> float:
    """Высота центра Солнца над горизонтом, градусы.

    Args:
        moment: момент времени; без часового пояса — UTC.
        lat_deg: широта, градусы (север — плюс).
        lon_deg: долгота, градусы (восток — плюс).

    Returns:
        Высота, градусы: отрицательная — Солнце под горизонтом.
    """
    jc = _julian_century(moment)
    mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360.0
    mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    eccentricity = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    anom = math.radians(mean_anom)
    center = (
        math.sin(anom) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(2 * anom) * (0.019993 - 0.000101 * jc)
        + math.sin(3 * anom) * 0.000289
    )
    omega = math.radians(125.04 - 1934.136 * jc)
    apparent_long = mean_long + center - 0.00569 - 0.00478 * math.sin(omega)
    arc_seconds = 21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))
    mean_obliquity = 23.0 + (26.0 + arc_seconds / 60.0) / 60.0
    obliquity = math.radians(mean_obliquity + 0.00256 * math.cos(omega))
    declination = math.asin(math.sin(obliquity) * math.sin(math.radians(apparent_long)))

    y = math.tan(obliquity / 2.0) ** 2
    long_rad = math.radians(mean_long)
    equation_of_time_min = 4.0 * math.degrees(
        y * math.sin(2 * long_rad)
        - 2 * eccentricity * math.sin(anom)
        + 4 * eccentricity * y * math.sin(anom) * math.cos(2 * long_rad)
        - 0.5 * y * y * math.sin(4 * long_rad)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * anom)
    )
    utc = as_utc(moment)
    minutes = utc.hour * 60.0 + utc.minute + (utc.second + utc.microsecond / 1e6) / 60.0
    solar_minutes = (minutes + equation_of_time_min + 4.0 * lon_deg) % 1440.0
    hour_angle = math.radians(solar_minutes / 4.0 - 180.0)
    lat = math.radians(lat_deg)
    cos_zenith = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(
        declination
    ) * math.cos(hour_angle)
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


def daylight_windows(
    start: datetime,
    lat_deg: float,
    lon_deg: float,
    min_elevation_deg: float,
    horizon_s: float | None = None,
) -> list[tuple[float, float]]:
    """Окна, когда Солнце не ниже порога, в секундах от `start`.

    Высота считается с шагом `DAYLIGHT_SAMPLE_STEP_S`, края окон уточняются
    делением пополам до секунды.

    Args:
        start: начало работ, UTC.
        lat_deg: широта точки, градусы.
        lon_deg: долгота точки, градусы.
        min_elevation_deg: порог высоты Солнца, градусы.
        horizon_s: на сколько вперёд искать, секунды; None —
            `DAYLIGHT_HORIZON_DAYS` суток.

    Returns:
        Окна (начало, конец) по порядку, секунды от `start`. Окно, открытое в
        момент `start`, начинается с 0.
    """
    horizon = (
        horizon_s if horizon_s is not None else assumptions.DAYLIGHT_HORIZON_DAYS * _SECONDS_PER_DAY
    )
    step = assumptions.DAYLIGHT_SAMPLE_STEP_S

    def above(offset_s: float) -> bool:
        moment = start + timedelta(seconds=offset_s)
        return sun_elevation_deg(moment, lat_deg, lon_deg) >= min_elevation_deg

    def edge(low_s: float, high_s: float) -> float:
        """Момент смены знака между low и high (у low — как у low), секунды."""
        state = above(low_s)
        while high_s - low_s > 1.0:
            middle = (low_s + high_s) / 2.0
            if above(middle) == state:
                low_s = middle
            else:
                high_s = middle
        return high_s

    windows: list[tuple[float, float]] = []
    opened: float | None = 0.0 if above(0.0) else None
    previous = 0.0
    offset = step
    while offset <= horizon:
        now = above(offset)
        if now and opened is None:
            opened = edge(previous, offset)
        elif not now and opened is not None:
            windows.append((opened, edge(previous, offset)))
            opened = None
        previous = offset
        offset += step
    if opened is not None:
        windows.append((opened, horizon))
    return windows


__all__ = ["daylight_windows", "sun_elevation_deg"]
