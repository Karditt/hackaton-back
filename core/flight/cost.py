"""Сборка вылета: фазы полёта, время с учётом ветра, расход энергии.

ЗАГЛУШКА E4. Инвариант 7 соблюдён по форме — время НЕ считается как
«длина / скорость», участки разделены по фазам, путевая скорость берётся из
треугольника скоростей. Заглушечное здесь:

* геометрия разворота схематична: длина берётся как полуокружность
  минимального радиуса, но траектория петли не строится;
* нет схемы разворота «через галс», когда шаг меньше диаметра разворота;
* расход энергии — средняя мощность из ёмкости АКБ, умноженная на
  коэффициент фазы; зависимости от массы, высоты и температуры нет;
* удлинение галсов за границу полигона под разворот не делается.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from core import assumptions
from core.flight.wind import ground_speed_mps
from core.schemas import (
    FlightPhase,
    Leg,
    MetricLineString,
    MetricPoint,
    PlanWarning,
    Severity,
    Sortie,
    Stage,
    Strip,
    SurveyParams,
    Vehicle,
    Waypoint,
    Wind,
)

SECONDS_PER_HOUR = 3600.0

#: Как маршрутизируется перелёт между двумя точками: прямая либо обход
#: запретных зон. Реализация приходит из `core.transit`, чтобы модель полёта
#: не знала про графы видимости.
TransitRouter = Callable[[MetricPoint, MetricPoint], tuple[MetricLineString, list[PlanWarning]]]


def _azimuth(start: MetricPoint, end: MetricPoint) -> float:
    """Азимут отрезка, градусы от севера по часовой стрелке."""
    return math.degrees(math.atan2(end.x_m - start.x_m, end.y_m - start.y_m)) % 360.0


def _distance_m(start: MetricPoint, end: MetricPoint) -> float:
    """Длина отрезка в метрах."""
    return math.hypot(end.x_m - start.x_m, end.y_m - start.y_m)


def average_power_w(vehicle: Vehicle) -> float | None:
    """Средняя мощность борта в горизонтальном полёте.

    Выводится из паспортной ёмкости АКБ и паспортной продолжительности полёта:
    P = E / T. Это оценка сверху снизу-вверх, но оба числа — из официального
    документа, поэтому допущением является только сама модель, не данные.

    Args:
        vehicle: борт из каталога.

    Returns:
        Средняя мощность, Вт. None — энергия АКБ неизвестна (пробел каталога).
    """
    if vehicle.energy_wh is None or vehicle.endurance_s <= 0:
        return None
    return vehicle.energy_wh / (vehicle.endurance_s / SECONDS_PER_HOUR)


def _energy_wh(power_w: float | None, phase: FlightPhase, duration_s: float) -> float | None:
    """Расход энергии на участке, Вт·ч."""
    if power_w is None:
        return None
    fraction = assumptions.POWER_FRACTION_BY_PHASE[phase.value]
    return power_w * fraction * duration_s / SECONDS_PER_HOUR


def _polyline_leg(
    phase: FlightPhase,
    line: MetricLineString,
    altitude_m: float,
    airspeed_mps: float,
    wind: Wind,
    power_w: float | None,
    strip_id: str | None = None,
) -> Leg:
    """Собрать участок по ломаной, считая путевую скорость посегментно.

    Args:
        phase: фаза полёта.
        line: ломаная в метрах рабочей проекции.
        altitude_m: высота участка над точкой взлёта, метры.
        airspeed_mps: воздушная скорость на участке, м/с.
        wind: ветер, метеорологическая конвенция.
        power_w: средняя мощность борта, Вт. None — энергия не считается.
        strip_id: идентификатор галса для фазы STRIP.

    Returns:
        Участок полёта с длиной, длительностью и расходом энергии.
    """
    points = line.points
    total_length_m = 0.0
    total_duration_s = 0.0
    waypoints: list[Waypoint] = []

    for index, (start, end) in enumerate(zip(points, points[1:], strict=False)):
        segment_m = _distance_m(start, end)
        if segment_m <= 0.0:
            continue
        speed_mps = ground_speed_mps(_azimuth(start, end), airspeed_mps, wind)
        total_length_m += segment_m
        total_duration_s += segment_m / speed_mps
        if index == 0:
            waypoints.append(
                Waypoint(x_m=start.x_m, y_m=start.y_m, alt_m=altitude_m, ground_speed_mps=speed_mps)
            )
        waypoints.append(
            Waypoint(x_m=end.x_m, y_m=end.y_m, alt_m=altitude_m, ground_speed_mps=speed_mps)
        )

    if len(waypoints) < 2:
        first = points[0]
        waypoints = [
            Waypoint(x_m=first.x_m, y_m=first.y_m, alt_m=altitude_m, ground_speed_mps=0.0),
            Waypoint(x_m=first.x_m, y_m=first.y_m, alt_m=altitude_m, ground_speed_mps=0.0),
        ]

    return Leg(
        phase=phase,
        waypoints=waypoints,
        length_m=total_length_m,
        duration_s=total_duration_s,
        ground_speed_mps=total_length_m / total_duration_s if total_duration_s > 0 else 0.0,
        energy_wh=_energy_wh(power_w, phase, total_duration_s),
        strip_id=strip_id,
    )


def _vertical_leg(
    phase: FlightPhase,
    point: MetricPoint,
    from_alt_m: float,
    to_alt_m: float,
    rate_mps: float,
    power_w: float | None,
) -> Leg:
    """Вертикальный участок: набор или снижение над одной точкой."""
    duration_s = abs(to_alt_m - from_alt_m) / rate_mps if rate_mps > 0 else 0.0
    return Leg(
        phase=phase,
        waypoints=[
            Waypoint(x_m=point.x_m, y_m=point.y_m, alt_m=from_alt_m, ground_speed_mps=0.0),
            Waypoint(x_m=point.x_m, y_m=point.y_m, alt_m=to_alt_m, ground_speed_mps=0.0),
        ],
        length_m=abs(to_alt_m - from_alt_m),
        duration_s=duration_s,
        ground_speed_mps=0.0,
        energy_wh=_energy_wh(power_w, phase, duration_s),
    )


def _ground_leg(
    phase: FlightPhase, point: MetricPoint, duration_s: float, power_w: float | None
) -> Leg:
    """Наземная фаза: взлёт или посадка. Длина по земле нулевая."""
    return Leg(
        phase=phase,
        waypoints=[
            Waypoint(x_m=point.x_m, y_m=point.y_m, alt_m=0.0, ground_speed_mps=0.0),
            Waypoint(x_m=point.x_m, y_m=point.y_m, alt_m=0.0, ground_speed_mps=0.0),
        ],
        length_m=0.0,
        duration_s=duration_s,
        ground_speed_mps=0.0,
        energy_wh=_energy_wh(power_w, phase, duration_s),
    )


def _turn_leg(
    vehicle: Vehicle,
    start: MetricPoint,
    end: MetricPoint,
    altitude_m: float,
    airspeed_mps: float,
    power_w: float | None,
) -> Leg | None:
    """Разворот борта самолётного типа между галсами.

    TODO(E4): длина взята как полуокружность минимального радиуса
    (π·r), но сама петля не строится — waypoints совпадают с концами
    соседних галсов. Для мультиротора участок не создаётся: разворот на месте.
    """
    if vehicle.type.value != "fixed_wing" or vehicle.turn_radius_m <= 0.0:
        return None
    length_m = math.pi * vehicle.turn_radius_m
    duration_s = length_m / airspeed_mps if airspeed_mps > 0 else 0.0
    return Leg(
        phase=FlightPhase.TURN,
        waypoints=[
            Waypoint(x_m=start.x_m, y_m=start.y_m, alt_m=altitude_m, ground_speed_mps=airspeed_mps),
            Waypoint(x_m=end.x_m, y_m=end.y_m, alt_m=altitude_m, ground_speed_mps=airspeed_mps),
        ],
        length_m=length_m,
        duration_s=duration_s,
        ground_speed_mps=airspeed_mps,
        energy_wh=_energy_wh(power_w, FlightPhase.TURN, duration_s),
    )


def build_sortie(
    sortie_id: str,
    vehicle: Vehicle,
    params: SurveyParams,
    site_id: str,
    site: MetricPoint,
    strips: list[Strip],
    wind: Wind,
    transit: TransitRouter,
    start_time_s: float = 0.0,
    vehicle_instance: int = 0,
    trip_index: int = 0,
) -> tuple[Sortie, list[PlanWarning]]:
    """Собрать один вылет: от взлёта до посадки, по фазам.

    Args:
        sortie_id: идентификатор вылета.
        vehicle: борт из каталога.
        params: параметры съёмки этого борта (высота, скорость на галсе).
        site_id: идентификатор площадки взлёта-посадки.
        site: площадка в метрах рабочей проекции.
        strips: галсы вылета в порядке облёта.
        wind: ветер, метеорологическая конвенция.
        transit: маршрутизатор перелётов между точками.
        start_time_s: момент взлёта от начала работ, секунды.
        vehicle_instance: номер экземпляра борта внутри строки флота.
        trip_index: номер вылета этого борта, с нуля.

    Returns:
        Пара «вылет, предупреждения».

    Raises:
        InfeasibleError: ветер не позволяет выдержать курс хотя бы на одном
            участке.
    """
    power_w = average_power_w(vehicle)
    altitude_m = params.altitude_agl_m
    cruise_mps = vehicle.cruise_speed_mps
    warnings: list[PlanWarning] = []
    legs: list[Leg] = [
        _ground_leg(FlightPhase.TAKEOFF, site, assumptions.TAKEOFF_TIME_S, power_w),
        _vertical_leg(
            FlightPhase.CLIMB, site, 0.0, altitude_m, assumptions.CLIMB_RATE_MPS, power_w
        ),
    ]

    position = site
    for index, strip in enumerate(strips):
        line, transit_warnings = transit(position, strip.start())
        warnings.extend(transit_warnings)
        legs.append(_polyline_leg(FlightPhase.TRANSIT, line, altitude_m, cruise_mps, wind, power_w))
        if index > 0:
            turn = _turn_leg(vehicle, position, strip.start(), altitude_m, cruise_mps, power_w)
            if turn is not None:
                legs.append(turn)
        legs.append(
            _polyline_leg(
                FlightPhase.STRIP,
                strip.line,
                strip.altitude_agl_m,
                params.survey_speed_mps,
                wind,
                power_w,
                strip_id=strip.id,
            )
        )
        position = strip.end()

    line, transit_warnings = transit(position, site)
    warnings.extend(transit_warnings)
    legs.append(_polyline_leg(FlightPhase.TRANSIT, line, altitude_m, cruise_mps, wind, power_w))
    legs.append(
        _vertical_leg(
            FlightPhase.DESCENT, site, altitude_m, 0.0, assumptions.DESCENT_RATE_MPS, power_w
        )
    )
    legs.append(_ground_leg(FlightPhase.LANDING, site, assumptions.LANDING_TIME_S, power_w))

    duration_s = sum(leg.duration_s for leg in legs)
    distance_m = sum(leg.length_m for leg in legs)
    energies = [leg.energy_wh for leg in legs if leg.energy_wh is not None]
    energy_wh = sum(energies) if len(energies) == len(legs) else None

    energy_fraction: float | None = None
    if energy_wh is not None and vehicle.energy_wh:
        energy_fraction = energy_wh / vehicle.energy_wh
    else:
        warnings.append(
            PlanWarning(
                code="flight.energy_unknown",
                severity=Severity.WARNING,
                stage=Stage.FLIGHT,
                message=(
                    f"{vehicle.name}: энергия АКБ в официальном источнике отсутствует "
                    "(пробел каталога), энергетический баланс вылета не проверен — "
                    "выполнимость оценена только по продолжительности полёта"
                ),
                context={"vehicle_id": vehicle.id, "sortie_id": sortie_id},
            )
        )

    sortie = Sortie(
        id=sortie_id,
        vehicle_id=vehicle.id,
        vehicle_instance=vehicle_instance,
        site_id=site_id,
        trip_index=trip_index,
        legs=legs,
        start_time_s=start_time_s,
        duration_s=duration_s,
        distance_m=distance_m,
        energy_wh=energy_wh,
        energy_fraction_used=energy_fraction,
        strip_ids=[strip.id for strip in strips],
    )
    return sortie, warnings


def estimate_sortie_duration_s(
    vehicle: Vehicle,
    params: SurveyParams,
    site: MetricPoint,
    strips: list[Strip],
    wind: Wind,
) -> float:
    """Быстрая оценка длительности вылета без сборки объектов.

    Нужна распределению (E6), которое проверяет вместимость вылета на каждом
    шаге: полная сборка `Sortie` с валидацией pydantic на таком цикле
    становится узким местом. Считается по той же модели фаз, что и
    `build_sortie`, поэтому расхождение возможно только в округлении.

    TODO(E4): держать две реализации одной модели рискованно — при усложнении
    модели полёта свести их в одну, вынеся расчёт фаз в общую функцию.

    Args:
        vehicle: борт из каталога.
        params: параметры съёмки борта.
        site: площадка взлёта-посадки, метры.
        strips: галсы вылета в порядке облёта.
        wind: ветер, метеорологическая конвенция.

    Returns:
        Оценка длительности вылета, секунды.
    """
    altitude_m = params.altitude_agl_m
    total_s = assumptions.TAKEOFF_TIME_S + assumptions.LANDING_TIME_S
    total_s += altitude_m / assumptions.CLIMB_RATE_MPS
    total_s += altitude_m / assumptions.DESCENT_RATE_MPS

    position = site
    for index, strip in enumerate(strips):
        total_s += _segment_seconds(position, strip.start(), vehicle.cruise_speed_mps, wind)
        if index > 0 and vehicle.turn_radius_m > 0.0:
            total_s += math.pi * vehicle.turn_radius_m / vehicle.cruise_speed_mps
        points = strip.line.points
        for start, end in zip(points, points[1:], strict=False):
            total_s += _segment_seconds(start, end, params.survey_speed_mps, wind)
        position = strip.end()
    total_s += _segment_seconds(position, site, vehicle.cruise_speed_mps, wind)
    return total_s


def _segment_seconds(
    start: MetricPoint, end: MetricPoint, airspeed_mps: float, wind: Wind
) -> float:
    """Время прохождения отрезка с учётом ветра, секунды."""
    distance_m = _distance_m(start, end)
    if distance_m <= 0.0:
        return 0.0
    return distance_m / ground_speed_mps(_azimuth(start, end), airspeed_mps, wind)


def endurance_limit_s(vehicle: Vehicle) -> float:
    """Предельная длительность вылета с учётом неснижаемого резерва, секунды."""
    return vehicle.endurance_s * (1.0 - vehicle.battery_reserve_fraction)


def sortie_is_feasible(sortie: Sortie, vehicle: Vehicle) -> tuple[bool, str]:
    """Проверить вылет по продолжительности и энергии.

    Args:
        sortie: собранный вылет.
        vehicle: борт из каталога.

    Returns:
        Пара «выполним, причина». Причина пуста, если вылет выполним.
    """
    limit_s = endurance_limit_s(vehicle)
    if sortie.duration_s > limit_s:
        return False, (
            f"длительность {sortie.duration_s / 60:.0f} мин превышает "
            f"{limit_s / 60:.0f} мин (автономность минус резерв "
            f"{vehicle.battery_reserve_fraction * 100:.0f}%)"
        )
    if sortie.energy_fraction_used is not None:
        available = 1.0 - vehicle.battery_reserve_fraction
        if sortie.energy_fraction_used > available:
            return False, (
                f"расход {sortie.energy_fraction_used * 100:.0f}% ёмкости превышает "
                f"доступные {available * 100:.0f}%"
            )
    return True, ""


__all__ = [
    "SECONDS_PER_HOUR",
    "TransitRouter",
    "average_power_w",
    "build_sortie",
    "endurance_limit_s",
    "estimate_sortie_duration_s",
    "sortie_is_feasible",
]
