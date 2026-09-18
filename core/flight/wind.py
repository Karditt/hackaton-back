"""Ветер и треугольник скоростей.

ЗДЕСЬ ЖИВЁТ ИНВАРИАНТ 4. `Wind.direction_deg` — метеорологическая конвенция:
направление, ОТКУДА дует. Перевод «откуда» → «куда» делается ровно в одном
месте, в `wind_vector`, и больше нигде в проекте знак ветра не трогается.
Перепутанный знак даёт зеркальный план и обнаруживается только на демонстрации.

Роль ветра (заказчик, №45): на галсе автопилот держит ПУТЕВУЮ скорость,
поэтому ветер не меняет время съёмки, а определяет выполнимость — хватит ли
борту воздушной скорости (`required_airspeed_mps`). `ground_speed_mps`
остаётся для случаев, когда задана воздушная скорость, а не путевая.

Система координат: X рабочей проекции на восток, Y на север. Азимут — от
севера по часовой стрелке, как принято в навигации.
"""

from __future__ import annotations

import math

from core.errors import InfeasibleError
from core.schemas import Wind


def wind_vector(wind: Wind) -> tuple[float, float]:
    """Вектор ветра в рабочей проекции — КУДА дует.

    Args:
        wind: ветер в метеорологической конвенции (`direction_deg` — откуда
            дует: 0° — с севера, 90° — с востока).

    Returns:
        Пара (составляющая на восток, составляющая на север), м/с.
        Северный ветер (0°) даёт вектор, направленный на юг: (0, −V).
    """
    blowing_to_deg = (wind.direction_deg + 180.0) % 360.0
    radians = math.radians(blowing_to_deg)
    return (wind.speed_mps * math.sin(radians), wind.speed_mps * math.cos(radians))


def _course_unit(course_deg: float) -> tuple[float, float]:
    """Единичный вектор путевого курса.

    Args:
        course_deg: путевой курс, градусы от севера по часовой стрелке.

    Returns:
        Пара (на восток, на север), безразмерная.
    """
    radians = math.radians(course_deg % 360.0)
    return (math.sin(radians), math.cos(radians))


def headwind_mps(course_deg: float, wind: Wind) -> float:
    """Продольная составляющая ветра вдоль курса.

    Args:
        course_deg: путевой курс, градусы.
        wind: ветер, метеорологическая конвенция.

    Returns:
        Составляющая, м/с. Положительная — попутная, отрицательная — встречная.
    """
    wind_x, wind_y = wind_vector(wind)
    course_x, course_y = _course_unit(course_deg)
    return wind_x * course_x + wind_y * course_y


def crosswind_mps(course_deg: float, wind: Wind) -> float:
    """Боковая составляющая ветра относительно курса.

    Args:
        course_deg: путевой курс, градусы.
        wind: ветер, метеорологическая конвенция.

    Returns:
        Составляющая, м/с. Знак указывает сторону сноса, для проверок
        используется модуль.
    """
    wind_x, wind_y = wind_vector(wind)
    course_x, course_y = _course_unit(course_deg)
    return wind_x * course_y - wind_y * course_x


def crab_angle_deg(course_deg: float, airspeed_mps: float, wind: Wind) -> float:
    """Угол упреждения, который борт держит, чтобы идти заданным курсом.

    Args:
        course_deg: путевой курс, градусы.
        airspeed_mps: воздушная скорость, м/с.
        wind: ветер, метеорологическая конвенция.

    Returns:
        Угол упреждения, градусы.

    Raises:
        InfeasibleError: боковая составляющая ветра не слабее воздушной
            скорости — курс физически не выдерживается.
    """
    if airspeed_mps <= 0.0:
        raise InfeasibleError("Воздушная скорость должна быть положительной")
    cross = crosswind_mps(course_deg, wind)
    ratio = cross / airspeed_mps
    if abs(ratio) >= 1.0:
        raise InfeasibleError(
            f"Боковой ветер {abs(cross):.1f} м/с не слабее воздушной скорости "
            f"{airspeed_mps:.1f} м/с — курс {course_deg:.0f}° невыдерживаем"
        )
    return math.degrees(math.asin(ratio))


def ground_speed_mps(course_deg: float, airspeed_mps: float, wind: Wind) -> float:
    """Путевая скорость на заданном курсе с учётом ветра.

    Треугольник скоростей: борт парирует боковую составляющую упреждением,
    поэтому вдоль курса остаётся sqrt(V² − Wбок²), к чему добавляется
    продольная составляющая ветра.

    Args:
        course_deg: путевой курс, градусы от севера по часовой стрелке.
        airspeed_mps: воздушная скорость, м/с.
        wind: ветер, метеорологическая конвенция (откуда дует).

    Returns:
        Путевая скорость, м/с. Всегда положительна.

    Raises:
        InfeasibleError: курс не выдерживается либо ветер сносит борт назад.
    """
    if airspeed_mps <= 0.0:
        raise InfeasibleError("Воздушная скорость должна быть положительной")
    cross = crosswind_mps(course_deg, wind)
    if abs(cross) >= airspeed_mps:
        raise InfeasibleError(
            f"Боковой ветер {abs(cross):.1f} м/с не слабее воздушной скорости "
            f"{airspeed_mps:.1f} м/с — курс {course_deg:.0f}° невыдерживаем"
        )
    along = headwind_mps(course_deg, wind)
    speed = along + math.sqrt(airspeed_mps**2 - cross**2)
    if speed <= 0.0:
        raise InfeasibleError(
            f"Встречный ветер сносит борт назад на курсе {course_deg:.0f}°: "
            f"воздушная скорость {airspeed_mps:.1f} м/с, ветер {wind.speed_mps:.1f} м/с"
        )
    return speed


def required_airspeed_mps(course_deg: float, ground_speed_mps: float, wind: Wind) -> float:
    """Воздушная скорость, нужная, чтобы идти курсом с заданной путевой скоростью.

    Треугольник скоростей наоборот: вектор воздушной скорости = вектор путевой
    минус вектор ветра (куда дует). Против ветра нужно больше, по ветру меньше.

    Args:
        course_deg: путевой курс, градусы от севера по часовой стрелке.
        ground_speed_mps: путевая скорость, которую держит автопилот, м/с.
        wind: ветер, метеорологическая конвенция (откуда дует).

    Returns:
        Модуль воздушной скорости, м/с.
    """
    wind_x, wind_y = wind_vector(wind)
    course_x, course_y = _course_unit(course_deg)
    return math.hypot(ground_speed_mps * course_x - wind_x, ground_speed_mps * course_y - wind_y)


__all__ = [
    "crab_angle_deg",
    "crosswind_mps",
    "ground_speed_mps",
    "headwind_mps",
    "required_airspeed_mps",
    "wind_vector",
]
