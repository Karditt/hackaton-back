"""Расписание вылетов и разведение бортов по времени (E14).

Расписание — обязательный выход (заказчик, №31). Разведение — главное, что
проверяет заказчик (№4): ни одна пара бортов не должна сближаться ближе
минимума разделения (№37).

ЗАГЛУШКА E14: вылеты ставятся на ось времени по очереди (сначала первые
вылеты всех бортов, потом вторые…). Старт вылета сдвигается шагом
`SCHEDULE_SHIFT_STEP_S`, пока его траектория конфликтует с уже поставленными
или пересекает временную запретную зону в её окне. Ожидание — на земле: так
борт не тратит заряд. Не делается: перестроение маршрута ради разведения,
круг ожидания в воздухе, выбор эшелона по загрузке.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point

from core import assumptions
from core.flight.cost import retime_sortie
from core.flight.maneuver import ManeuverWindow, maneuver_overlap, maneuver_windows
from core.flight.track import Track, build_track, first_conflict
from core.geo.mask import WorkArea, ZoneArea
from core.schemas import (
    CoverageSet,
    FlightPhase,
    PlanWarning,
    Scenario,
    Schedule,
    ScheduleEntry,
    Severity,
    Sortie,
    Stage,
    Vehicle,
)

_GROUND = (FlightPhase.TAKEOFF, FlightPhase.LANDING)


@dataclass(frozen=True)
class _ZoneWindow:
    """Участок вылета, проходящий через временную зону, в секундах от взлёта."""

    zone: ZoneArea
    from_s: float
    to_s: float


def _temporal_crossings(
    sortie: Sortie, zones: list[ZoneArea], coverage: CoverageSet | None
) -> list[_ZoneWindow]:
    """Участки вылета и зоны манёвра внутри временных зон на запрещённых высотах."""
    temporal = [zone for zone in zones if not zone.permanent]
    if not temporal:
        return []
    windows: list[_ZoneWindow] = []
    for leg in sortie.legs:
        if leg.phase in _GROUND:
            continue
        points = [(waypoint.x_m, waypoint.y_m) for waypoint in leg.waypoints]
        shape = LineString(points) if len({*points}) > 1 else Point(points[0])
        low = min(waypoint.alt_m for waypoint in leg.waypoints)
        high = max(waypoint.alt_m for waypoint in leg.waypoints)
        for zone in temporal:
            if zone.blocks_altitude(low, high) and shape.intersects(zone.inner):
                windows.append(_ZoneWindow(zone, leg.waypoints[0].time_s, leg.waypoints[-1].time_s))

    # Зона манёвра самолёта занята всё время съёмки своей подобласти.
    for item in maneuver_windows(sortie, coverage, is_fixed_wing=True):
        for zone in temporal:
            if zone.blocks_altitude(
                sortie.survey_altitude_m, sortie.survey_altitude_m
            ) and item.footprint.intersects(zone.inner):
                windows.append(_ZoneWindow(zone, item.from_s, item.to_s))
    return windows


def _zone_shift(start_s: float, windows: list[_ZoneWindow]) -> float:
    """На сколько надо сдвинуть старт, чтобы не попасть во временные зоны."""
    shift = 0.0
    for window in windows:
        if window.zone.active_during(start_s + window.from_s, start_s + window.to_s):
            shift = max(shift, window.zone.active_to_s - window.from_s - start_s)
    return shift


def build_schedule(
    sorties: list[Sortie],
    vehicles: dict[str, Vehicle],
    work_area: WorkArea,
    scenario: Scenario,
    coverage: CoverageSet | None = None,
) -> tuple[list[Sortie], Schedule]:
    """Поставить вылеты на общую ось времени с разведением бортов.

    Args:
        sorties: вылеты со временем от взлёта (`start_time_s` = 0).
        vehicles: каталог бортов.
        work_area: рабочая область (временные зоны).
        scenario: сценарий (время начала работ, межвылетная подготовка).
        coverage: покрытие — зоны манёвра самолётов тоже не должны попадать
            во временные зоны в их окно.

    Returns:
        Пара «вылеты на общей оси времени, расписание». Вылет, для которого
        окно без конфликтов не нашлось, ставится на самое раннее время и
        помечается ошибкой — валидатор объявит план невыполнимым.
    """
    turnaround_s = (
        scenario.turnaround_s
        if scenario.turnaround_s is not None
        else assumptions.TURNAROUND_TIME_S
    )
    order = sorted(range(len(sorties)), key=lambda index: (sorties[index].trip_index, index))
    ready_at: dict[tuple[str, int], float] = {}
    placed_tracks: list[Track] = []
    placed_zones: list[ManeuverWindow] = []
    placed: dict[int, Sortie] = {}
    entries: list[ScheduleEntry] = []
    warnings: list[PlanWarning] = []

    for index in order:
        sortie = sorties[index]
        vehicle = vehicles[sortie.vehicle_id]
        key = (sortie.vehicle_id, sortie.vehicle_instance)
        ready_s = ready_at.get(key, assumptions.VEHICLE_PREP_TIME_S)
        track = build_track(sortie, vehicle.is_fixed_wing())
        zones = maneuver_windows(sortie, coverage, vehicle.is_fixed_wing())
        windows = _temporal_crossings(sortie, work_area.zones, coverage)

        start_s = ready_s
        resolved = False
        while start_s - ready_s <= assumptions.SCHEDULE_MAX_DELAY_S:
            shift = _zone_shift(start_s, windows)
            if shift > 0.0:
                if math.isinf(shift):
                    break
                start_s += shift
                continue
            candidate = track.shifted(start_s)
            if any(first_conflict(candidate, other) for other in placed_tracks):
                start_s += assumptions.SCHEDULE_SHIFT_STEP_S
                continue
            # Зона манёвра занята всё время съёмки подобласти, а не только
            # когда через неё идёт линия маршрута (долг №27).
            moved = [zone.shifted(start_s) for zone in zones]
            if any(maneuver_overlap(zone, other) for zone in moved for other in placed_zones):
                start_s += assumptions.SCHEDULE_SHIFT_STEP_S
                continue
            resolved = True
            break

        if not resolved:
            warnings.append(
                PlanWarning(
                    code="deconflict.unresolved",
                    severity=Severity.ERROR,
                    stage=Stage.SCHEDULE,
                    message=(
                        f"Вылет {sortie.id}: за {assumptions.SCHEDULE_MAX_DELAY_S / 3600:.0f} ч "
                        "сдвигов старта не нашлось окна без сближений с другими бортами (включая "
                        "зоны манёвра) и без временных запретных зон"
                    ),
                    context={"sortie_id": sortie.id, "vehicle_id": sortie.vehicle_id},
                )
            )
            start_s = ready_s

        scheduled = retime_sortie(sortie, start_s)
        placed[index] = scheduled
        placed_tracks.append(track.shifted(start_s))
        placed_zones.extend(zone.shifted(start_s) for zone in zones)
        ready_at[key] = scheduled.end_time_s() + turnaround_s
        entries.append(
            ScheduleEntry(
                sortie_id=sortie.id,
                vehicle_id=sortie.vehicle_id,
                vehicle_instance=sortie.vehicle_instance,
                trip_index=sortie.trip_index,
                takeoff_site_id=sortie.takeoff_site_id,
                landing_site_id=sortie.landing_site_id,
                start_s=start_s,
                end_s=scheduled.end_time_s(),
                delay_s=start_s - ready_s,
                transit_altitude_m=sortie.transit_altitude_m,
            )
        )

    warnings.append(
        PlanWarning(
            code="stub.schedule",
            severity=Severity.WARNING,
            stage=Stage.SCHEDULE,
            message=(
                "Разведение бортов — только сдвигом старта на земле. Маршрут ради "
                "разведения не перестраивается, круг ожидания в воздухе не используется "
                "(TODO(E14))"
            ),
        )
    )
    entries.sort(key=lambda entry: (entry.start_s, entry.sortie_id))
    schedule = Schedule(
        start_time=scenario.start_time, entries=entries, warnings=warnings, stub=True
    )
    return [placed[index] for index in range(len(sorties))], schedule


__all__ = ["build_schedule"]
