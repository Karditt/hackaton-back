"""Инвариант 4: направление ветра — метеорологическое, «откуда дует».

Ошибка знака здесь даёт зеркальный план и на глаз не видна. Поэтому
конвенция закреплена тестами, а не комментарием.
"""

from __future__ import annotations

import math

import pytest

from core.errors import InfeasibleError
from core.flight.wind import (
    crab_angle_deg,
    crosswind_mps,
    ground_speed_mps,
    headwind_mps,
    required_airspeed_mps,
    wind_vector,
)
from core.schemas import Wind


def test_north_wind_blows_southward() -> None:
    """Ветер 0° — СЕВЕРНЫЙ: дует с севера на юг, вектор смотрит на юг."""
    east, north = wind_vector(Wind(speed_mps=10.0, direction_deg=0.0))
    assert east == pytest.approx(0.0, abs=1e-9)
    assert north == pytest.approx(-10.0)


def test_west_wind_blows_eastward() -> None:
    """Ветер 270° — ЗАПАДНЫЙ: дует с запада на восток."""
    east, north = wind_vector(Wind(speed_mps=8.0, direction_deg=270.0))
    assert east == pytest.approx(8.0)
    assert north == pytest.approx(0.0, abs=1e-9)


def test_tailwind_is_faster_than_headwind() -> None:
    """Попутный ветер ускоряет, встречный — замедляет. Знак не перепутан."""
    wind = Wind(speed_mps=5.0, direction_deg=180.0)  # южный: дует на север
    northbound = ground_speed_mps(0.0, 20.0, wind)
    southbound = ground_speed_mps(180.0, 20.0, wind)
    assert northbound == pytest.approx(25.0)
    assert southbound == pytest.approx(15.0)
    assert northbound > southbound


def test_headwind_component_sign() -> None:
    """Продольная составляющая положительна при попутном ветре."""
    wind = Wind(speed_mps=6.0, direction_deg=180.0)
    assert headwind_mps(0.0, wind) == pytest.approx(6.0)
    assert headwind_mps(180.0, wind) == pytest.approx(-6.0)


def test_pure_crosswind_slows_the_aircraft() -> None:
    """Чистый боковой ветер не сносит по курсу, но съедает путевую скорость."""
    wind = Wind(speed_mps=6.0, direction_deg=270.0)  # западный
    speed = ground_speed_mps(0.0, 10.0, wind)  # курс на север
    assert abs(crosswind_mps(0.0, wind)) == pytest.approx(6.0)
    assert speed == pytest.approx(math.sqrt(10.0**2 - 6.0**2))


def test_crab_angle_points_into_the_wind() -> None:
    """Угол упреждения ненулевой при боковом ветре и нулевой при продольном."""
    crosswind = Wind(speed_mps=5.0, direction_deg=270.0)
    assert abs(crab_angle_deg(0.0, 10.0, crosswind)) == pytest.approx(math.degrees(math.asin(0.5)))
    tailwind = Wind(speed_mps=5.0, direction_deg=180.0)
    assert crab_angle_deg(0.0, 10.0, tailwind) == pytest.approx(0.0, abs=1e-9)


def test_crosswind_stronger_than_airspeed_is_infeasible() -> None:
    """Курс, который нельзя выдержать, обязан падать понятной ошибкой."""
    wind = Wind(speed_mps=12.0, direction_deg=270.0)
    with pytest.raises(InfeasibleError):
        ground_speed_mps(0.0, 10.0, wind)


def test_headwind_stronger_than_airspeed_is_infeasible() -> None:
    """Встречный ветер сильнее воздушной скорости — борт сносит назад."""
    wind = Wind(speed_mps=15.0, direction_deg=0.0)
    with pytest.raises(InfeasibleError):
        ground_speed_mps(0.0, 10.0, wind)


def test_calm_air_ground_speed_equals_airspeed() -> None:
    """Штиль: путевая скорость равна воздушной на любом курсе."""
    calm = Wind(speed_mps=0.0, direction_deg=0.0)
    for course in (0.0, 45.0, 137.0, 270.0, 359.0):
        assert ground_speed_mps(course, 12.0, calm) == pytest.approx(12.0)


def test_headwind_needs_more_airspeed_to_hold_ground_speed() -> None:
    """№45: автопилот держит путевую скорость — против ветра нужна большая воздушная."""
    wind = Wind(speed_mps=5.0, direction_deg=0.0)  # северный: дует на юг
    assert required_airspeed_mps(0.0, 10.0, wind) == pytest.approx(15.0)  # на север — против
    assert required_airspeed_mps(180.0, 10.0, wind) == pytest.approx(5.0)  # на юг — по ветру


def test_crosswind_airspeed_is_the_hypotenuse() -> None:
    """Боковой ветер: воздушная скорость — гипотенуза путевой и ветра."""
    wind = Wind(speed_mps=6.0, direction_deg=270.0)
    assert required_airspeed_mps(0.0, 8.0, wind) == pytest.approx(10.0)
