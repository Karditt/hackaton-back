"""Сборка вылета: фазы полёта, время, линейный расход, высоты.

Модель — по ответам заказчика:

* время вылета — сумма фаз (инвариант 7); на галсе и на перелёте автопилот
  держит путевую скорость, поэтому время участка — длина, делённая на эту
  скорость (№45). Ветер во время не входит: он учтён при выборе скорости в
  модели съёмки и проверяется валидатором как выполнимость;
* расход заряда линеен по полётному времени, отсечка 20% (№34, №35);
* полезная работа — на одной высоте; высота меняется только на перелётах,
  которые идут на эшелоне внутри коридора (№10);
* площадки взлёта и посадки могут различаться (№29);
* разворот самолёта — время по радиусу разворота; геометрия петли не
  строится, вместо неё у подобласти есть защитная зона манёвра (№38).

Одна модель на всё: распределение проверяет вместимость вылета той же
функцией, что собирает итоговый вылет. Быстрой оценки-двойника больше нет.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from core import assumptions
from core.schemas import (
    CellCoverage,
    FlightPhase,
    Leg,
    MetricLineString,
    MetricPoint,
    Sortie,
    SurveyParams,
    Vehicle,
    Waypoint,
)

#: Как прокладывается перелёт между двумя точками на заданной высоте, м AGL.
#: Реализация — `core.transit`, чтобы модель полёта не знала про графы видимости.
TransitRouter = Callable[[MetricPoint, MetricPoint, float], MetricLineString]


def _distance_m(start: MetricPoint, end: MetricPoint) -> float:
    """Длина отрезка в метрах."""
    return math.hypot(end.x_m - start.x_m, end.y_m - start.y_m)


class _SortieBuilder:
    """Накопитель участков вылета с общим счётчиком времени и положением."""

    def __init__(self, start: MetricPoint) -> None:
        self.legs: list[Leg] = []
        self.time_s = 0.0
        self.position = start
        self.altitude_m = 0.0

    def ground(self, phase: FlightPhase, duration_s: float) -> None:
        """Наземная фаза (взлёт, посадка): длина по земле нулевая."""
        start = Waypoint(
            x_m=self.position.x_m, y_m=self.position.y_m, alt_m=0.0, time_s=self.time_s
        )
        self.time_s += duration_s
        end = Waypoint(x_m=self.position.x_m, y_m=self.position.y_m, alt_m=0.0, time_s=self.time_s)
        self.legs.append(
            Leg(
                phase=phase,
                waypoints=[start, end],
                length_m=0.0,
                duration_s=duration_s,
                ground_speed_mps=0.0,
            )
        )
        self.altitude_m = 0.0

    def vertical(self, to_alt_m: float) -> None:
        """Набор или снижение над текущей точкой. Нулевой перепад — без участка."""
        delta_m = to_alt_m - self.altitude_m
        if abs(delta_m) <= assumptions.GEOMETRY_TOLERANCE_M:
            return
        phase = FlightPhase.CLIMB if delta_m > 0 else FlightPhase.DESCENT
        rate = assumptions.CLIMB_RATE_MPS if delta_m > 0 else assumptions.DESCENT_RATE_MPS
        duration_s = abs(delta_m) / rate
        start = Waypoint(
            x_m=self.position.x_m,
            y_m=self.position.y_m,
            alt_m=self.altitude_m,
            ground_speed_mps=0.0,
            time_s=self.time_s,
        )
        self.time_s += duration_s
        end = Waypoint(
            x_m=self.position.x_m,
            y_m=self.position.y_m,
            alt_m=to_alt_m,
            ground_speed_mps=0.0,
            time_s=self.time_s,
        )
        self.legs.append(
            Leg(
                phase=phase,
                waypoints=[start, end],
                length_m=abs(delta_m),
                duration_s=duration_s,
                ground_speed_mps=0.0,
            )
        )
        self.altitude_m = to_alt_m

    def polyline(
        self,
        phase: FlightPhase,
        points: list[MetricPoint],
        speed_mps: float,
        strip_id: str | None = None,
        cell_id: str | None = None,
    ) -> None:
        """Горизонтальный участок по ломаной на текущей высоте с путевой скоростью."""
        waypoints = [
            Waypoint(
                x_m=points[0].x_m,
                y_m=points[0].y_m,
                alt_m=self.altitude_m,
                ground_speed_mps=speed_mps,
                time_s=self.time_s,
            )
        ]
        length_m = 0.0
        for start, end in zip(points, points[1:], strict=False):
            segment_m = _distance_m(start, end)
            if segment_m <= 0.0:
                continue
            length_m += segment_m
            self.time_s += segment_m / speed_mps
            waypoints.append(
                Waypoint(
                    x_m=end.x_m,
                    y_m=end.y_m,
                    alt_m=self.altitude_m,
                    ground_speed_mps=speed_mps,
                    time_s=self.time_s,
                )
            )
        if len(waypoints) < 2:
            return
        self.legs.append(
            Leg(
                phase=phase,
                waypoints=waypoints,
                length_m=length_m,
                duration_s=length_m / speed_mps,
                ground_speed_mps=speed_mps,
                strip_id=strip_id,
                cell_id=cell_id,
            )
        )
        self.position = points[-1]

    def turn(self, end: MetricPoint, length_m: float, speed_mps: float, cell_id: str) -> None:
        """Разворот между галсами: время по длине манёвра, геометрия — отрезок.

        TODO(E4): траектория петли не строится намеренно — заказчик просил
        заменить её защитной зоной манёвра (№38); время считается по длине.
        """
        if length_m <= assumptions.GEOMETRY_TOLERANCE_M:
            self.position = end
            return
        duration_s = length_m / speed_mps
        start = Waypoint(
            x_m=self.position.x_m,
            y_m=self.position.y_m,
            alt_m=self.altitude_m,
            ground_speed_mps=speed_mps,
            time_s=self.time_s,
        )
        self.time_s += duration_s
        finish = Waypoint(
            x_m=end.x_m,
            y_m=end.y_m,
            alt_m=self.altitude_m,
            ground_speed_mps=speed_mps,
            time_s=self.time_s,
        )
        self.legs.append(
            Leg(
                phase=FlightPhase.TURN,
                waypoints=[start, finish],
                length_m=length_m,
                duration_s=duration_s,
                ground_speed_mps=speed_mps,
                cell_id=cell_id,
            )
        )
        self.position = end


def turn_length_m(vehicle: Vehicle, start: MetricPoint, end: MetricPoint) -> float:
    """Длина перехода между концом галса и началом следующего, метры.

    Мультиротор переходит по прямой. Самолёт не может развернуться короче
    полуокружности минимального радиуса.
    """
    gap_m = _distance_m(start, end)
    if vehicle.is_fixed_wing() and vehicle.turn_radius_m > 0.0:
        return max(math.pi * vehicle.turn_radius_m, gap_m)
    return gap_m


def build_sortie(
    sortie_id: str,
    vehicle: Vehicle,
    params: SurveyParams,
    takeoff_site_id: str,
    takeoff_site: MetricPoint,
    landing_site_id: str,
    landing_site: MetricPoint,
    cells: list[CellCoverage],
    transit_altitude_m: float,
    router: TransitRouter,
    own_area: BaseGeometry | None = None,
    vehicle_instance: int = 0,
    trip_index: int = 0,
) -> Sortie:
    """Собрать один вылет от взлёта до посадки. Время — от момента взлёта.

    Между подобластями одного вылета борт идёт на высоте съёмки, только если
    прямая не выходит за его собственные подобласти: над чужими он проходит
    на своём эшелоне (инвариант 9 — разведение по вертикали).

    Args:
        sortie_id: идентификатор вылета.
        vehicle: борт.
        params: параметры съёмки борта (высота, путевые скорости).
        takeoff_site_id: идентификатор площадки взлёта.
        takeoff_site: площадка взлёта, метры.
        landing_site_id: идентификатор площадки посадки.
        landing_site: площадка посадки, метры.
        cells: покрытия подобластей в порядке облёта.
        transit_altitude_m: эшелон перелёта, м AGL.
        router: маршрутизатор перелётов.
        own_area: объединение подобластей вылета, метры. None — между
            подобластями всегда через эшелон.
        vehicle_instance: номер экземпляра борта внутри строки флота.
        trip_index: номер вылета этого борта, с нуля.

    Returns:
        Вылет со временем, отсчитанным от взлёта (`start_time_s` = 0).
        Расписание сдвигает его на общую ось времени (`retime_sortie`).
    """
    survey_alt_m = params.altitude_agl_m
    hop_area = own_area.buffer(assumptions.BOUNDARY_TOLERANCE_M) if own_area is not None else None
    builder = _SortieBuilder(takeoff_site)

    builder.ground(FlightPhase.TAKEOFF, assumptions.TAKEOFF_TIME_S)
    builder.vertical(transit_altitude_m)

    for cell in cells:
        if not cell.strips:
            continue
        entry = cell.strips[0].start()
        on_survey_level = abs(builder.altitude_m - survey_alt_m) <= assumptions.GEOMETRY_TOLERANCE_M
        hop = LineString([(builder.position.x_m, builder.position.y_m), (entry.x_m, entry.y_m)])
        if on_survey_level and hop_area is not None and hop.within(hop_area):
            builder.polyline(
                FlightPhase.TRANSIT,
                [builder.position, entry],
                params.transit_speed_mps,
                cell_id=cell.cell_id,
            )
        else:
            builder.vertical(transit_altitude_m)
            line = router(builder.position, entry, transit_altitude_m)
            builder.polyline(FlightPhase.TRANSIT, line.points, params.transit_speed_mps)
            builder.vertical(survey_alt_m)

        for index, strip in enumerate(cell.strips):
            if index > 0:
                target = strip.start()
                hop = LineString(
                    [(builder.position.x_m, builder.position.y_m), (target.x_m, target.y_m)]
                )
                if hop_area is None or hop.within(hop_area):
                    builder.turn(
                        target,
                        turn_length_m(vehicle, builder.position, target),
                        params.transit_speed_mps,
                        cell.cell_id,
                    )
                else:
                    # Переход через отверстие подобласти (зону, препятствие) —
                    # в обход, на той же высоте съёмки.
                    line = router(builder.position, target, survey_alt_m)
                    builder.polyline(
                        FlightPhase.TRANSIT,
                        line.points,
                        params.transit_speed_mps,
                        cell_id=cell.cell_id,
                    )
            builder.polyline(
                FlightPhase.STRIP,
                list(strip.line.points),
                params.survey_speed_mps,
                strip_id=strip.id,
                cell_id=cell.cell_id,
            )

    builder.vertical(transit_altitude_m)
    line = router(builder.position, landing_site, transit_altitude_m)
    builder.polyline(FlightPhase.TRANSIT, line.points, params.transit_speed_mps)
    builder.vertical(0.0)
    builder.ground(FlightPhase.LANDING, assumptions.LANDING_TIME_S)

    duration_s = builder.time_s
    return Sortie(
        id=sortie_id,
        vehicle_id=vehicle.id,
        vehicle_instance=vehicle_instance,
        takeoff_site_id=takeoff_site_id,
        landing_site_id=landing_site_id,
        trip_index=trip_index,
        legs=builder.legs,
        start_time_s=0.0,
        duration_s=duration_s,
        distance_m=sum(leg.length_m for leg in builder.legs if leg.phase not in _VERTICAL),
        energy_fraction_used=duration_s / vehicle.endurance_s,
        survey_altitude_m=survey_alt_m,
        transit_altitude_m=transit_altitude_m,
        cell_ids=[cell.cell_id for cell in cells],
        strip_ids=[strip.id for cell in cells for strip in cell.strips],
    )


_VERTICAL = (FlightPhase.CLIMB, FlightPhase.DESCENT)


def retime_sortie(sortie: Sortie, start_time_s: float) -> Sortie:
    """Перенести вылет на общую ось времени: взлёт в момент `start_time_s`.

    Args:
        sortie: вылет.
        start_time_s: момент взлёта от начала работ, секунды.

    Returns:
        Копия вылета со сдвинутыми временами всех точек.
    """
    delta_s = start_time_s - sortie.start_time_s
    legs = [
        leg.model_copy(
            update={
                "waypoints": [
                    waypoint.model_copy(update={"time_s": waypoint.time_s + delta_s})
                    for waypoint in leg.waypoints
                ]
            }
        )
        for leg in sortie.legs
    ]
    return sortie.model_copy(update={"legs": legs, "start_time_s": start_time_s})


def endurance_limit_s(vehicle: Vehicle) -> float:
    """Предельная длительность вылета с учётом отсечки заряда, секунды."""
    return vehicle.endurance_s * (1.0 - vehicle.battery_reserve_fraction)


def exceeds_range(sortie: Sortie, vehicle: Vehicle) -> bool:
    """True — маршрут вылета длиннее паспортной протяжённости для этой модели.

    Ограничение живёт отдельно от заряда: у самолёта запас хода может кончиться
    раньше, чем автономность, и наоборот. Борт без `max_range_m` в каталоге
    (Геоскан 801, Gemini — числа нет в документе) по протяжённости не
    ограничивается: выдумывать предел нельзя (инвариант 6).
    """
    return vehicle.max_range_m is not None and sortie.distance_m > vehicle.max_range_m


def sortie_is_feasible(sortie: Sortie, vehicle: Vehicle) -> tuple[bool, str]:
    """Проверить вылет по заряду: линейная модель, отсечка 20% (№34, №35).

    Протяжённость маршрута здесь НЕ проверяется — она отдельным правилом,
    `exceeds_range`, чтобы причина невыполнимости называлась своим именем.

    Args:
        sortie: собранный вылет.
        vehicle: борт из каталога.

    Returns:
        Пара «выполним, причина». Причина пуста, если вылет выполним.
    """
    available = 1.0 - vehicle.battery_reserve_fraction
    if sortie.energy_fraction_used > available:
        return False, (
            f"полётное время {sortie.duration_s / 60:.0f} мин — это "
            f"{sortie.energy_fraction_used * 100:.0f}% автономности, а доступно "
            f"{available * 100:.0f}% (отсечка {vehicle.battery_reserve_fraction * 100:.0f}%)"
        )
    return True, ""


__all__ = [
    "TransitRouter",
    "build_sortie",
    "endurance_limit_s",
    "exceeds_range",
    "retime_sortie",
    "sortie_is_feasible",
    "turn_length_m",
]
