"""Расписание вылетов и разведение бортов по времени (E14).

Расписание — обязательный выход (заказчик, №31). Разведение — главное, что
проверяет заказчик (№4): ни одна пара бортов не должна сближаться ближе
минимума разделения (№37).

ЗАГЛУШКА E14: вылеты ставятся на ось времени по очереди (сначала первые
вылеты всех бортов, потом вторые… — либо в порядке, который задало
распределение). Старт вылета сдвигается шагом `SCHEDULE_SHIFT_STEP_S`, пока
его траектория конфликтует с уже поставленными или пересекает временную
запретную зону в её окне. Ожидание — на земле: так борт не тратит заряд. Не
делается: перестроение маршрута ради разведения, круг ожидания в воздухе,
выбор эшелона по загрузке.

Поставленные вылеты живут в `ScheduleBook`. Её же ведёт распределение (E6),
когда собирает вылеты: так оно видит сдвиг старта, который вылет получит в
расписании (долг №54), и может собрать другой вылет вместо задержанного.
Порядок постановки, заданный распределением, расписание повторяет — и
получает те же старты.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

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
    cell_id: str | None = None
    """Подобласть, над которой идёт участок; None — перелёт, набор, снижение."""


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
                windows.append(
                    _ZoneWindow(
                        zone, leg.waypoints[0].time_s, leg.waypoints[-1].time_s, leg.cell_id
                    )
                )

    # Зона манёвра самолёта занята всё время съёмки своей подобласти.
    for item in maneuver_windows(sortie, coverage, is_fixed_wing=True):
        for zone in temporal:
            if zone.blocks_altitude(
                sortie.survey_altitude_m, sortie.survey_altitude_m
            ) and item.footprint.intersects(zone.inner):
                windows.append(_ZoneWindow(zone, item.from_s, item.to_s, item.cell_id))
    return windows


def _zone_shift(start_s: float, windows: list[_ZoneWindow]) -> float:
    """На сколько надо сдвинуть старт, чтобы не попасть во временные зоны."""
    shift = 0.0
    for window in windows:
        if window.zone.active_during(start_s + window.from_s, start_s + window.to_s):
            shift = max(shift, window.zone.active_to_s - window.from_s - start_s)
    return shift


def _survey_span(sortie: Sortie) -> tuple[float, float] | None:
    """Съёмка вылета — от начала первого галса до конца последнего, секунды от взлёта."""
    strips = [leg for leg in sortie.legs if leg.phase is FlightPhase.STRIP]
    if not strips:
        return None
    base = sortie.start_time_s
    return strips[0].waypoints[0].time_s - base, strips[-1].waypoints[-1].time_s - base


def daylight_shift(
    start_s: float, span: tuple[float, float] | None, windows: list[tuple[float, float]] | None
) -> float:
    """На сколько сдвинуть старт, чтобы съёмка легла в окно по Солнцу, секунды (долг №55).

    Съёмка вылета — от первого галса до последнего — должна целиком лечь в
    одно окно; перелёты, взлёт и посадка — в любое время.

    Args:
        start_s: взлёт, секунды от начала работ.
        span: съёмка от взлёта, секунды (`_survey_span`); None — галсов нет.
        windows: окна по порядку, секунды от начала работ; None — ограничения нет.

    Returns:
        Сдвиг, секунды: 0 — ложится как есть, ∞ — не ложится ни в одно окно.
    """
    if windows is None or span is None:
        return 0.0
    begin, end = start_s + span[0], start_s + span[1]
    for window_from, window_to in windows:
        shift = max(window_from - begin, 0.0)
        if end + shift <= window_to:
            return shift
    return math.inf


@dataclass(frozen=True)
class PreparedSortie:
    """Вылет, готовый к постановке: траектория и занятые им зоны от момента взлёта."""

    sortie: Sortie
    track: Track
    maneuvers: list[ManeuverWindow]
    zone_windows: list[_ZoneWindow]
    survey_span: tuple[float, float] | None = None
    """Съёмка — от первого галса до последнего, секунды от взлёта."""


def _conflict_times(candidate: Track, other: Track) -> np.ndarray:
    """Моменты сближения двух траекторий — те же отсчёты, что у `first_conflict`, секунды."""
    start = max(candidate.airborne_from_s, other.airborne_from_s)
    end = min(candidate.airborne_to_s, other.airborne_to_s)
    if end <= start:
        return np.empty(0)
    required = max(candidate.separation_m, other.separation_m)
    times = np.append(np.arange(start, end, assumptions.SEPARATION_SAMPLE_STEP_S), end)
    ax, ay, az = candidate.position_at(times)
    bx, by, bz = other.position_at(times)
    hits = (np.hypot(ax - bx, ay - by) < required) & (
        np.abs(az - bz) < assumptions.VERTICAL_SEPARATION_M
    )
    return times[hits]


class ScheduleBook:
    """Поставленные на общую ось времени вылеты: траектории и зоны манёвра.

    Общая для расписания и распределения: распределение ставит сюда вылеты по
    мере сборки и видит сдвиг старта, который вылет получит (долг №54).
    """

    def __init__(
        self, zones: list[ZoneArea], daylight: list[tuple[float, float]] | None = None
    ) -> None:
        """Пустая книга.

        Args:
            zones: запретные зоны рабочей области; временные из них сдвигают старт.
            daylight: окна съёмки по высоте Солнца, секунды от начала работ
                (`WorkArea.daylight`); None — ограничения нет.
        """
        self._zones = zones
        self._daylight = daylight
        self._tracks: list[Track] = []
        self._maneuvers: list[ManeuverWindow] = []
        self._sizes: list[int] = []
        """Число зон манёвра после каждого поставленного вылета — для отката."""

    def prepare(
        self, sortie: Sortie, is_fixed_wing: bool, coverage: CoverageSet | None
    ) -> PreparedSortie:
        """Собрать траекторию и занятые зоны вылета, время — от взлёта.

        Args:
            sortie: вылет со временем от взлёта.
            is_fixed_wing: самолётный тип — минимум разделения.
            coverage: покрытие с зонами манёвра подобластей вылета; None — зон нет.
        """
        return PreparedSortie(
            sortie=sortie,
            track=build_track(sortie, is_fixed_wing),
            maneuvers=maneuver_windows(sortie, coverage, is_fixed_wing),
            zone_windows=_temporal_crossings(sortie, self._zones, coverage),
            survey_span=_survey_span(sortie),
        )

    def _clear(self, prepared: PreparedSortie, start_s: float) -> bool:
        """Нет сближений траекторий и зон манёвра с поставленными при этом старте."""
        candidate = prepared.track.shifted(start_s)
        if any(first_conflict(candidate, other) for other in self._tracks):
            return False
        # Зона манёвра занята всё время съёмки подобласти, а не только когда
        # через неё идёт линия маршрута (долг №27).
        moved = [zone.shifted(start_s) for zone in prepared.maneuvers]
        return not any(maneuver_overlap(zone, other) for zone in moved for other in self._maneuvers)

    def earliest_start(self, prepared: PreparedSortie, ready_s: float) -> float | None:
        """Самый ранний старт без сближений и без временных зон в их окне, секунды.

        Старт сдвигается от `ready_s` шагом `SCHEDULE_SHIFT_STEP_S`, через
        временную зону — сразу до её закрытия, через ночь (съёмка не ложится
        в окно по Солнцу) — сразу к следующему окну. Ночь в предел сдвига
        `SCHEDULE_MAX_DELAY_S` не входит: это не ожидание соседа.

        Args:
            prepared: вылет.
            ready_s: когда борт готов к вылету, секунды от начала работ.

        Returns:
            Момент взлёта, секунды; None — за `SCHEDULE_MAX_DELAY_S` окна нет.
        """
        start_s = ready_s
        night_s = 0.0
        while start_s - ready_s - night_s <= assumptions.SCHEDULE_MAX_DELAY_S:
            dark = daylight_shift(start_s, prepared.survey_span, self._daylight)
            if dark > 0.0:
                if math.isinf(dark):
                    return None
                start_s += dark
                night_s += dark
                continue
            shift = _zone_shift(start_s, prepared.zone_windows)
            if shift > 0.0:
                if math.isinf(shift):
                    return None
                start_s += shift
                continue
            if self._clear(prepared, start_s):
                return start_s
            start_s += assumptions.SCHEDULE_SHIFT_STEP_S
        return None

    def blocking_cells(self, prepared: PreparedSortie, start_s: float) -> set[str]:
        """Подобласти вылета, из-за которых нельзя взлететь в `start_s`.

        Над ними при этом старте траектория сходится с поставленными вылетами,
        их зона манёвра — с чужими зонами манёвра, или они во временной зоне в
        её окне. Сближения на перелётах, наборе и снижении ни к одной
        подобласти не относятся и сюда не попадают.

        Args:
            prepared: вылет.
            start_s: момент взлёта, секунды от начала работ.
        """
        cells: set[str] = set()
        for window in prepared.zone_windows:
            if window.cell_id is not None and window.zone.active_during(
                start_s + window.from_s, start_s + window.to_s
            ):
                cells.add(window.cell_id)
        for zone in prepared.maneuvers:
            moved = zone.shifted(start_s)
            if any(maneuver_overlap(moved, other) for other in self._maneuvers):
                cells.add(zone.cell_id)
        candidate = prepared.track.shifted(start_s)
        times = [_conflict_times(candidate, other) for other in self._tracks]
        hits = np.concatenate(times) if times else np.empty(0)
        if hits.size:
            for leg in prepared.sortie.legs:
                if leg.cell_id is None:
                    continue
                begin = start_s + leg.waypoints[0].time_s
                end = start_s + leg.waypoints[-1].time_s
                if np.any((hits >= begin) & (hits <= end)):
                    cells.add(leg.cell_id)
        return cells

    def busy_footprint(self, altitude_m: float, from_s: float, to_s: float) -> BaseGeometry:
        """Где в этот промежуток заняты зоны манёвра на близкой высоте, метры.

        Объединение поставленных зон манёвра, занятых хотя бы часть промежутка
        и разведённых по высоте меньше чем на вертикальный интервал. Буфера
        разделения здесь нет — его добавляет тот, кто спрашивает.

        Args:
            altitude_m: высота съёмки, м AGL.
            from_s: начало промежутка, секунды от начала работ.
            to_s: конец промежутка, секунды от начала работ.
        """
        return unary_union(
            [
                zone.footprint
                for zone in self._maneuvers
                if zone.from_s < to_s
                and zone.to_s > from_s
                and abs(zone.altitude_m - altitude_m) < assumptions.VERTICAL_SEPARATION_M
            ]
        )

    def closed_footprint(self, altitude_m: float, from_s: float, to_s: float) -> BaseGeometry:
        """Где в этот промежуток действуют временные зоны, запрещающие высоту, метры.

        Args:
            altitude_m: высота съёмки, м AGL.
            from_s: начало промежутка, секунды от начала работ.
            to_s: конец промежутка, секунды от начала работ.
        """
        return unary_union(
            [
                zone.inner
                for zone in self._zones
                if not zone.permanent
                and zone.active_during(from_s, to_s)
                and zone.blocks_altitude(altitude_m, altitude_m)
            ]
        )

    def daylight_room(self, ready_s: float) -> list[float] | None:
        """Сколько времени дают окна по Солнцу, начиная с `ready_s`, секунды.

        Returns:
            Остаток текущего окна (или всё следующее, если сейчас ночь) и
            длины следующих окон по порядку; пусто — окон больше нет; None —
            ограничения по Солнцу нет.
        """
        if self._daylight is None:
            return None
        return [
            window_to - max(window_from, ready_s)
            for window_from, window_to in self._daylight
            if window_to > ready_s
        ]

    def closing_zones(self, altitude_m: float) -> list[tuple[float, BaseGeometry]]:
        """Временные зоны с моментом открытия, запрещающие высоту, метры.

        Зона, которая откроется позже, закроет подобласти под собой до своего
        закрытия: их выгодно снять раньше (распределение, «срочная» работа).

        Args:
            altitude_m: высота съёмки, м AGL.

        Returns:
            Пары «момент открытия, секунды от начала работ; полигон зоны».
            Зоны, действующие с начала работ (открытый интервал), не входят.
        """
        return [
            (zone.active_from_s, zone.inner)
            for zone in self._zones
            if not zone.permanent
            and not math.isinf(zone.active_from_s)
            and zone.blocks_altitude(altitude_m, altitude_m)
        ]

    def add(self, prepared: PreparedSortie, start_s: float) -> None:
        """Поставить вылет со взлётом в `start_s` секунд от начала работ."""
        self._tracks.append(prepared.track.shifted(start_s))
        self._maneuvers.extend(zone.shifted(start_s) for zone in prepared.maneuvers)
        self._sizes.append(len(self._maneuvers))

    def __len__(self) -> int:
        """Сколько вылетов поставлено."""
        return len(self._tracks)

    def rewind(self, count: int) -> None:
        """Оставить только первые `count` поставленных вылетов."""
        del self._tracks[count:]
        del self._sizes[count:]
        del self._maneuvers[self._sizes[-1] if self._sizes else 0 :]


def build_schedule(
    sorties: list[Sortie],
    vehicles: dict[str, Vehicle],
    work_area: WorkArea,
    scenario: Scenario,
    coverage: CoverageSet | None = None,
    keep_order: bool = False,
) -> tuple[list[Sortie], Schedule]:
    """Поставить вылеты на общую ось времени с разведением бортов.

    Args:
        sorties: вылеты со временем от взлёта (`start_time_s` = 0).
        vehicles: каталог бортов.
        work_area: рабочая область (временные зоны).
        scenario: сценарий (время начала работ, межвылетная подготовка).
        coverage: покрытие — зоны манёвра самолётов тоже не должны попадать
            во временные зоны в их окно.
        keep_order: ставить вылеты в порядке списка — так, как их ставило
            распределение, когда собирало (вылеты одного борта в нём идут по
            номеру). False — сначала первые вылеты всех бортов, потом вторые.

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
    order = (
        list(range(len(sorties)))
        if keep_order
        else sorted(range(len(sorties)), key=lambda index: (sorties[index].trip_index, index))
    )
    ready_at: dict[tuple[str, int], float] = {}
    book = ScheduleBook(work_area.zones, work_area.daylight)
    placed: dict[int, Sortie] = {}
    entries: list[ScheduleEntry] = []
    warnings: list[PlanWarning] = []

    for index in order:
        sortie = sorties[index]
        vehicle = vehicles[sortie.vehicle_id]
        key = (sortie.vehicle_id, sortie.vehicle_instance)
        ready_s = ready_at.get(key, assumptions.VEHICLE_PREP_TIME_S)
        prepared = book.prepare(sortie, vehicle.is_fixed_wing(), coverage)
        found = book.earliest_start(prepared, ready_s)
        start_s = ready_s if found is None else found

        if found is None:
            warnings.append(
                PlanWarning(
                    code="deconflict.unresolved",
                    severity=Severity.ERROR,
                    stage=Stage.SCHEDULE,
                    message=(
                        f"Вылет {sortie.id}: за {assumptions.SCHEDULE_MAX_DELAY_S / 3600:.0f} ч "
                        "сдвигов старта не нашлось окна без сближений с другими бортами (включая "
                        "зоны манёвра) и без временных запретных зон"
                        + (
                            ", со съёмкой внутри окна по высоте Солнца"
                            if work_area.daylight is not None
                            else ""
                        )
                    ),
                    context={"sortie_id": sortie.id, "vehicle_id": sortie.vehicle_id},
                )
            )

        scheduled = retime_sortie(sortie, start_s)
        placed[index] = scheduled
        book.add(prepared, start_s)
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


__all__ = ["PreparedSortie", "ScheduleBook", "build_schedule"]
