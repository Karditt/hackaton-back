"""Независимый краш-тест готового плана (E7, P0).

Заказчик проверяет решения краш-тестом (№4): нет столкновений и опасных
сближений, нет залёта в запретные зоны, нет опасных пролётов у высотных
препятствий. Валидатор — наш собственный краш-тест. Он НЕ доверяет стадиям,
которые строили план: берёт готовые вылеты с временами и проверяет их заново.

Жёсткие правила (инвариант 9) дают НАРУШЕНИЯ: план с ними не выдаётся, задание
объявляется невыполнимым с причиной. Мягкие — предупреждения.

Досягаемость запасной площадки из любой точки маршрута (№41) — мягкая
проверка, предупреждение. Не проверяется (объявлено в отчёте): траектория
петли разворота (вместо неё — защитная зона манёвра), рельеф между
препятствиями (нет ЦМР).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from core import assumptions, text
from core.flight.cost import exceeds_range, sortie_is_feasible
from core.flight.maneuver import ManeuverWindow, maneuver_overlap, maneuver_windows
from core.flight.track import build_track, first_conflict
from core.flight.wind import required_airspeed_mps
from core.geo.mask import WorkArea, as_utc
from core.geo.sun import sun_elevation_deg
from core.schemas import (
    CoverageSet,
    Decomposition,
    FlightPhase,
    Leg,
    MetricLineString,
    MetricPoint,
    PlanWarning,
    Scenario,
    Severity,
    Sortie,
    Stage,
    Vehicle,
)

_GROUND = (FlightPhase.TAKEOFF, FlightPhase.LANDING)


@dataclass
class ValidationReport:
    """Итог проверки: нарушения жёстких правил и предупреждения."""

    violations: list[PlanWarning] = field(default_factory=list)
    warnings: list[PlanWarning] = field(default_factory=list)

    def ok(self) -> bool:
        """True — ни одного нарушения жёстких правил."""
        return not self.violations


def _violation(
    code: str, message: str, geometry: MetricLineString | None = None, **context: str
) -> PlanWarning:
    return PlanWarning(
        code=code,
        severity=Severity.ERROR,
        stage=Stage.VALIDATE,
        message=message,
        context=dict(context),
        geometry=geometry,
    )


def _leg_shape(leg: Leg) -> BaseGeometry:
    """Проекция участка на план: линия или точка (для вертикальных участков)."""
    points = [(waypoint.x_m, waypoint.y_m) for waypoint in leg.waypoints]
    return LineString(points) if len(set(points)) > 1 else Point(points[0])


def _leg_line(leg: Leg) -> MetricLineString:
    """Участок как ломаная контракта — для подсветки на карте."""
    return MetricLineString(
        points=[MetricPoint(x_m=waypoint.x_m, y_m=waypoint.y_m) for waypoint in leg.waypoints]
    )


def _airborne_legs(sortie: Sortie) -> list[Leg]:
    return [leg for leg in sortie.legs if leg.phase not in _GROUND]


def _names(vehicles: dict[str, Vehicle]) -> dict[str, str]:
    """Название модели по идентификатору — для подписи вылета в сообщении."""
    return {vehicle_id: vehicle.name for vehicle_id, vehicle in vehicles.items()}


def _named(name: str, fallback: str) -> str:
    """Объект сценария в сообщении: название в кавычках, без названия — идентификатор."""
    return f"«{name or fallback}»"


def _check_energy(sorties: list[Sortie], vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Заряд: линейная модель, отсечка 20% (№34, №35)."""
    items = []
    names = _names(vehicles)
    for sortie in sorties:
        feasible, reason = sortie_is_feasible(sortie, vehicles[sortie.vehicle_id])
        if not feasible:
            items.append(
                _violation(
                    "validate.energy",
                    f"{text.sortie_label(sortie, names)}: {reason}",
                    sortie_id=sortie.id,
                    vehicle_id=sortie.vehicle_id,
                )
            )
    return items


def _check_range(sorties: list[Sortie], vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Протяжённость маршрута против паспортной для модели БЛА (долг №43).

    Первая проверка, которую делает над полётным заданием сам GeoScan Planner
    заказчика: «Общая длина маршрута превышает допустимую для данной модели
    БЛА» (docs/dev/planner_reference.md §6). Запас хода — не то же самое, что
    автономность: у самолёта он может кончиться раньше времени полёта.
    """
    items = []
    for sortie in sorties:
        vehicle = vehicles[sortie.vehicle_id]
        if not exceeds_range(sortie, vehicle):
            continue
        assert vehicle.max_range_m is not None
        items.append(
            _violation(
                "validate.range",
                f"{text.sortie_label(sortie, _names(vehicles))}: маршрут "
                f"{text.num(sortie.distance_m / 1000)} км длиннее паспортной протяжённости "
                f"{vehicle.max_range_m / 1000:.0f} км",
                sortie_id=sortie.id,
                vehicle_id=sortie.vehicle_id,
            )
        )
    return items


def _check_wind(
    scenario: Scenario, sorties: list[Sortie], vehicles: dict[str, Vehicle]
) -> tuple[list[PlanWarning], list[PlanWarning]]:
    """Ветер: паспортный предел и удержание путевой скорости на каждом галсе (№45)."""
    violations: list[PlanWarning] = []
    warnings: list[PlanWarning] = []
    wind = scenario.wind
    for vehicle_id in sorted({sortie.vehicle_id for sortie in sorties}):
        vehicle = vehicles[vehicle_id]
        if wind.speed_mps > vehicle.max_wind_mps:
            violations.append(
                _violation(
                    "validate.wind_limit",
                    f"{vehicle.name}: ветер {text.num(wind.speed_mps)} м/с выше паспортного "
                    f"предела {text.num(vehicle.max_wind_mps)} м/с",
                    vehicle_id=vehicle_id,
                )
            )
        elif wind.speed_mps > vehicle.max_wind_mps * assumptions.WIND_SAFETY_FRACTION:
            warnings.append(
                PlanWarning(
                    code="validate.wind_near_limit",
                    severity=Severity.WARNING,
                    stage=Stage.VALIDATE,
                    message=(
                        f"{vehicle.name}: ветер {text.num(wind.speed_mps)} м/с — больше "
                        f"{assumptions.WIND_SAFETY_FRACTION * 100:.0f}% паспортного предела "
                        f"{text.num(vehicle.max_wind_mps)} м/с, запаса на порывы почти нет"
                    ),
                    context={"vehicle_id": vehicle_id},
                )
            )

    for sortie in sorties:
        vehicle = vehicles[sortie.vehicle_id]
        top = vehicle.max_speed_mps or vehicle.cruise_speed_mps
        bottom = vehicle.min_speed_mps if vehicle.is_fixed_wing() else None
        for leg in sortie.legs:
            if leg.phase is not FlightPhase.STRIP:
                continue
            first, last = leg.waypoints[0], leg.waypoints[-1]
            course = _course_deg(first.x_m, first.y_m, last.x_m, last.y_m)
            needed = required_airspeed_mps(course, leg.ground_speed_mps, wind)
            if needed > top + 1e-6 or (bottom is not None and needed < bottom - 1e-6):
                violations.append(
                    _violation(
                        "validate.airspeed",
                        f"{text.sortie_label(sortie, _names(vehicles))}: на галсе курсом "
                        f"{course:.0f}° нужна воздушная скорость {text.num(needed)} м/с — вне "
                        "диапазона борта",
                        _leg_line(leg),
                        sortie_id=sortie.id,
                        strip_id=leg.strip_id or "",
                    )
                )
                break
    return violations, warnings


def _course_deg(x0: float, y0: float, x1: float, y1: float) -> float:
    """Азимут отрезка, градусы от севера по часовой стрелке."""
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0


def _maneuver_footprints(sortie: Sortie, coverage: CoverageSet) -> list[ManeuverWindow]:
    """Зоны манёвра вылета с временем съёмки подобласти.

    Зоны манёвра строятся только самолётному типу (№38), поэтому минимум
    разделения у окна — самолётный; у мультиротора список пуст.
    """
    return maneuver_windows(sortie, coverage, is_fixed_wing=True)


def _check_zones(
    work_area: WorkArea,
    sorties: list[Sortie],
    coverage: CoverageSet | None,
    names: Mapping[str, str],
) -> list[PlanWarning]:
    """Запретные зоны в плане, по высоте и во времени (№40), с буфером."""
    items: list[PlanWarning] = []
    for sortie in sorties:
        label = text.sortie_label(sortie, names)
        reported: set[str] = set()
        for leg in _airborne_legs(sortie):
            shape = _leg_shape(leg)
            low = min(waypoint.alt_m for waypoint in leg.waypoints)
            high = max(waypoint.alt_m for waypoint in leg.waypoints)
            start, end = leg.waypoints[0].time_s, leg.waypoints[-1].time_s
            for zone in work_area.zones:
                if zone.id in reported:
                    continue
                if (
                    zone.blocks_altitude(low, high)
                    and zone.active_during(start, end)
                    and shape.intersects(zone.inner)
                ):
                    reported.add(zone.id)
                    limitation = (
                        " Обход не найден: точка маршрута внутри зоны, либо окно временной "
                        "зоны пришлось на перелёт"
                        if leg.phase is FlightPhase.TRANSIT
                        else ""
                    )
                    items.append(
                        _violation(
                            "validate.no_fly_zone",
                            f"{label}: участок «{text.PHASE_LABELS[leg.phase]}» входит в "
                            f"запретную зону {_named(zone.name, zone.id)} (с буфером "
                            f"{assumptions.NFZ_BUFFER_M:.0f} м) на высоте {low:.0f}–{high:.0f} м."
                            f"{limitation}",
                            _leg_line(leg),
                            sortie_id=sortie.id,
                            zone_id=zone.id,
                            phase=leg.phase.value,
                        )
                    )
        if coverage is None:
            continue
        for window in _maneuver_footprints(sortie, coverage):
            for zone in work_area.zones:
                if zone.id in reported:
                    continue
                if (
                    zone.blocks_altitude(sortie.survey_altitude_m, sortie.survey_altitude_m)
                    and zone.active_during(window.from_s, window.to_s)
                    and window.footprint.intersects(zone.inner)
                ):
                    reported.add(zone.id)
                    items.append(
                        _violation(
                            "validate.maneuver_zone",
                            f"{label}: зона манёвра самолёта в подобласти "
                            f"{window.cell_id} задевает запретную зону "
                            f"{_named(zone.name, zone.id)}",
                            sortie_id=sortie.id,
                            zone_id=zone.id,
                            cell_id=window.cell_id,
                        )
                    )
    return items


def _check_obstacles(
    work_area: WorkArea,
    sorties: list[Sortie],
    coverage: CoverageSet | None,
    names: Mapping[str, str],
) -> list[PlanWarning]:
    """Высотные препятствия: не ближе буфера в плане, если высота ниже безопасной (№4)."""
    items: list[PlanWarning] = []
    if not work_area.obstacles:
        return items
    for sortie in sorties:
        label = text.sortie_label(sortie, names)
        reported: set[str] = set()
        for leg in _airborne_legs(sortie):
            shape = _leg_shape(leg)
            low = min(waypoint.alt_m for waypoint in leg.waypoints)
            for obstacle in work_area.obstacles:
                if obstacle.id in reported:
                    continue
                if obstacle.blocks_altitude(low) and shape.intersects(obstacle.inner):
                    reported.add(obstacle.id)
                    items.append(
                        _violation(
                            "validate.obstacle",
                            f"{label}: участок «{text.PHASE_LABELS[leg.phase]}» на высоте "
                            f"{low:.0f} м проходит ближе {assumptions.OBSTACLE_BUFFER_M:.0f} м "
                            f"от препятствия {_named(obstacle.name, obstacle.id)} высотой "
                            f"{obstacle.height_m:.0f} м (безопасно выше "
                            f"{obstacle.safe_altitude_m:.0f} м)",
                            _leg_line(leg),
                            sortie_id=sortie.id,
                            obstacle_id=obstacle.id,
                        )
                    )
        if coverage is None:
            continue
        for window in _maneuver_footprints(sortie, coverage):
            for obstacle in work_area.obstacles:
                if obstacle.id in reported:
                    continue
                if obstacle.blocks_altitude(
                    sortie.survey_altitude_m
                ) and window.footprint.intersects(obstacle.inner):
                    reported.add(obstacle.id)
                    items.append(
                        _violation(
                            "validate.maneuver_zone",
                            f"{label}: зона манёвра самолёта в подобласти "
                            f"{window.cell_id} задевает препятствие "
                            f"{_named(obstacle.name, obstacle.id)}",
                            sortie_id=sortie.id,
                            obstacle_id=obstacle.id,
                            cell_id=window.cell_id,
                        )
                    )
    return items


def _check_airspace(
    work_area: WorkArea,
    sorties: list[Sortie],
    coverage: CoverageSet | None,
    names: Mapping[str, str],
) -> list[PlanWarning]:
    """Граница разрешённого воздушного пространства."""
    if work_area.airspace is None:
        return []
    allowed = work_area.airspace.buffer(assumptions.BOUNDARY_TOLERANCE_M)
    items: list[PlanWarning] = []
    for sortie in sorties:
        label = text.sortie_label(sortie, names)
        for leg in _airborne_legs(sortie):
            if not _leg_shape(leg).within(allowed):
                items.append(
                    _violation(
                        "validate.airspace",
                        f"{label}: участок «{text.PHASE_LABELS[leg.phase]}» выходит за "
                        "границу разрешённого воздушного пространства",
                        _leg_line(leg),
                        sortie_id=sortie.id,
                    )
                )
                break
        if coverage is None:
            continue
        for window in _maneuver_footprints(sortie, coverage):
            if not window.footprint.within(allowed):
                items.append(
                    _violation(
                        "validate.maneuver_zone",
                        f"{label}: зона манёвра в подобласти {window.cell_id} выходит "
                        "за границу разрешённого воздушного пространства",
                        sortie_id=sortie.id,
                        cell_id=window.cell_id,
                    )
                )
                break
    return items


def _check_altitudes(
    work_area: WorkArea, sorties: list[Sortie], names: Mapping[str, str]
) -> list[PlanWarning]:
    """Коридор высот и одна высота съёмки на вылет (№10)."""
    items: list[PlanWarning] = []
    for sortie in sorties:
        label = text.sortie_label(sortie, names)
        top = max(waypoint.alt_m for leg in sortie.legs for waypoint in leg.waypoints)
        if top > work_area.corridor_max_m + assumptions.BOUNDARY_TOLERANCE_M:
            items.append(
                _violation(
                    "validate.corridor",
                    f"{label}: высота {top:.0f} м выше коридора {work_area.corridor_max_m:.0f} м",
                    sortie_id=sortie.id,
                )
            )
        survey = {
            round(waypoint.alt_m, 1)
            for leg in sortie.legs
            if leg.phase is FlightPhase.STRIP
            for waypoint in leg.waypoints
        }
        if len(survey) > 1:
            items.append(
                _violation(
                    "validate.survey_altitude",
                    f"{label}: съёмка ведётся на разных высотах "
                    f"({', '.join(f'{value:.0f} м' for value in sorted(survey))}) — полезная "
                    "работа должна идти на одной",
                    sortie_id=sortie.id,
                )
            )
    return items


def _check_separation(sorties: list[Sortie], vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Попарное разведение бортов в пространстве и времени (№4, №37)."""
    tracks = [
        build_track(sortie, vehicles[sortie.vehicle_id].is_fixed_wing()) for sortie in sorties
    ]
    tracks.sort(key=lambda track: track.airborne_from_s)
    labels = {sortie.id: text.sortie_label(sortie, _names(vehicles)) for sortie in sorties}
    items: list[PlanWarning] = []
    for i, first in enumerate(tracks):
        for second in tracks[i + 1 :]:
            if second.airborne_from_s >= first.airborne_to_s:
                break
            conflict = first_conflict(first, second)
            if conflict is None:
                continue
            items.append(
                _violation(
                    "validate.separation",
                    f"Сближение: {labels.get(first.sortie_id, first.sortie_id)} и "
                    f"{labels.get(second.sortie_id, second.sortie_id)} на "
                    f"{text.num(conflict.time_s / 60)} мин от начала работ — "
                    f"{conflict.horizontal_m:.0f} м по горизонтали при минимуме "
                    f"{conflict.required_m:.0f} м и {conflict.vertical_m:.0f} м по вертикали "
                    f"при интервале {assumptions.VERTICAL_SEPARATION_M:.0f} м",
                    MetricLineString(
                        points=[
                            MetricPoint(x_m=conflict.x_m - 1.0, y_m=conflict.y_m),
                            MetricPoint(x_m=conflict.x_m + 1.0, y_m=conflict.y_m),
                        ]
                    ),
                    sortie_id=first.sortie_id,
                    other_sortie_id=second.sortie_id,
                )
            )
    return items


def _check_maneuver_separation(
    sorties: list[Sortie], coverage: CoverageSet | None, vehicles: dict[str, Vehicle]
) -> list[PlanWarning]:
    """Зоны манёвра разных вылетов друг относительно друга (долг №27, №37).

    Траекторий тут мало: разворот в траектории — отрезок между концами
    соседних галсов, а самолёт вправе крутить петлю где угодно внутри своей
    зоны (№38). Два самолёта, снимающие соседние подобласти на одной высоте в
    одно время, могут разойтись отрезками и сойтись на развороте.
    """
    windows: list[ManeuverWindow] = []
    for sortie in sorties:
        vehicle = vehicles[sortie.vehicle_id]
        windows.extend(maneuver_windows(sortie, coverage, vehicle.is_fixed_wing()))
    windows.sort(key=lambda item: item.from_s)
    labels = {sortie.id: text.sortie_label(sortie, _names(vehicles)) for sortie in sorties}

    items: list[PlanWarning] = []
    reported: set[tuple[str, str]] = set()
    for index, first in enumerate(windows):
        for second in windows[index + 1 :]:
            if second.from_s >= first.to_s:
                break
            if first.sortie_id == second.sortie_id:
                continue
            pair = tuple(sorted((first.sortie_id, second.sortie_id)))
            if pair in reported:
                continue
            overlap = maneuver_overlap(first, second)
            if overlap is None:
                continue
            reported.add(pair)
            items.append(
                _violation(
                    "validate.maneuver_separation",
                    f"Зоны манёвра сходятся: {labels.get(first.sortie_id, first.sortie_id)} "
                    f"(подобласть {first.cell_id}) и "
                    f"{labels.get(second.sortie_id, second.sortie_id)} (подобласть "
                    f"{second.cell_id}) заняты вместе с {text.num(overlap.from_s / 60)} по "
                    f"{text.num(overlap.to_s / 60)} мин от начала работ "
                    f"на {overlap.distance_m:.0f} м друг от друга при минимуме "
                    f"{overlap.required_m:.0f} м и разнице высот {overlap.vertical_m:.0f} м",
                    MetricLineString(
                        points=[
                            MetricPoint(x_m=overlap.x_m - 1.0, y_m=overlap.y_m),
                            MetricPoint(x_m=overlap.x_m + 1.0, y_m=overlap.y_m),
                        ]
                    ),
                    sortie_id=first.sortie_id,
                    other_sortie_id=second.sortie_id,
                    cell_id=first.cell_id,
                )
            )
    return items


def _check_coverage(decomposition: Decomposition, sorties: list[Sortie]) -> list[PlanWarning]:
    """Полнота: каждая подобласть снята каким-то вылетом (инвариант 10)."""
    flown = {cell_id for sortie in sorties for cell_id in sortie.cell_ids}
    missing = [cell.id for cell in decomposition.cells if cell.id not in flown]
    if not missing:
        return []
    verb = "не снята" if text.plural(len(missing), ("1", "2", "5")) == "1" else "не сняты"
    return [
        _violation(
            "validate.coverage_incomplete",
            f"{len(missing)} из {len(decomposition.cells)} подобластей {verb} ни одним "
            "вылетом — задание не выполнено целиком",
            cell_ids=",".join(missing),
        )
    ]


def _check_continuity(
    sorties: list[Sortie], names: Mapping[str, str], sites: Mapping[str, str]
) -> list[PlanWarning]:
    """Борт взлетает там, где сел, и не раньше, чем сел.

    Площадки взлёта и посадки одного вылета могут различаться (№29), но
    следующий вылет того же борта начинается с площадки, где закончился
    предыдущий: перевозку борта между площадками план не предусматривает.
    """
    items: list[PlanWarning] = []
    by_vehicle: dict[tuple[str, int], list[Sortie]] = {}
    for sortie in sorties:
        by_vehicle.setdefault((sortie.vehicle_id, sortie.vehicle_instance), []).append(sortie)
    for trips in by_vehicle.values():
        trips.sort(key=lambda sortie: sortie.start_time_s)
        for previous, current in zip(trips, trips[1:], strict=False):
            label = text.sortie_label(current, names)
            if current.takeoff_site_id != previous.landing_site_id:
                items.append(
                    _violation(
                        "validate.site_continuity",
                        f"{label} начинается с площадки "
                        f"{text.site_label(current.takeoff_site_id, sites)}, а предыдущий вылет "
                        f"борта закончился на {text.site_label(previous.landing_site_id, sites)}",
                        sortie_id=current.id,
                    )
                )
            if current.start_time_s < previous.end_time_s():
                items.append(
                    _violation(
                        "validate.vehicle_overlap",
                        f"{label} начинается раньше, чем борт сел после вылета "
                        f"{previous.trip_index + 1}",
                        sortie_id=current.id,
                    )
                )
    return items


def _check_deadline(scenario: Scenario, sorties: list[Sortie]) -> list[PlanWarning]:
    """Ограничение по сроку (№23)."""
    if scenario.deadline_s is None or not sorties:
        return []
    makespan = max(sortie.end_time_s() for sortie in sorties)
    if makespan <= scenario.deadline_s:
        return []
    return [
        _violation(
            "validate.deadline",
            f"Работы занимают {text.num(makespan / 3600, 2)} ч при ограничении "
            f"{text.num(scenario.deadline_s / 3600, 2)} ч",
        )
    ]


def _check_daylight(
    scenario: Scenario, work_area: WorkArea, sorties: list[Sortie], names: Mapping[str, str]
) -> list[PlanWarning]:
    """Галсы — при Солнце не ниже порога сценария (долг №55).

    Высота Солнца считается заново по времени начала и конца каждого галса в
    центре области — окна, по которым строилось расписание, не используются.
    Центр, а не точка галса — та же точка, по которой считаются окна: на
    область в десятки километров высота Солнца меняется меньше чем на 0,3°.
    """
    threshold = scenario.min_sun_elevation_deg
    if threshold is None or scenario.start_time is None:
        return []
    origin = as_utc(scenario.start_time)
    center = work_area.projector.xy_to_geo(work_area.area.centroid.x, work_area.area.centroid.y)
    items: list[PlanWarning] = []
    for sortie in sorties:
        for leg in sortie.legs:
            if leg.phase is not FlightPhase.STRIP:
                continue
            low = min(
                sun_elevation_deg(origin + timedelta(seconds=point.time_s), center.lat, center.lon)
                for point in (leg.waypoints[0], leg.waypoints[-1])
            )
            if low < threshold - assumptions.DAYLIGHT_TOLERANCE_DEG:
                items.append(
                    _violation(
                        "validate.daylight",
                        f"{text.sortie_label(sortie, names)}: галс снимается при Солнце "
                        f"{text.num(low)}° — ниже заданных {threshold:.0f}°",
                        _leg_line(leg),
                        sortie_id=sortie.id,
                        strip_id=leg.strip_id or "",
                    )
                )
                break
    return items


@dataclass
class _AlternateGap:
    """Худшая точка маршрута, откуда запасная площадка недосягаема."""

    sortie_id: str
    sortie_label: str
    point: MetricPoint
    site: MetricPoint
    site_id: str
    distance_m: float
    reach_m: float


def _alternates(
    scenario: Scenario, work_area: WorkArea, landing_site_id: str
) -> dict[str, MetricPoint]:
    """Запасные площадки вылета: все площадки сценария, кроме площадки посадки."""
    return {
        site.id: work_area.site(site.id) for site in scenario.sites if site.id != landing_site_id
    }


def _check_alternate_reach(
    scenario: Scenario, work_area: WorkArea, sorties: list[Sortie], vehicles: dict[str, Vehicle]
) -> list[PlanWarning]:
    """Запасная площадка досягаема из любой точки маршрута (№41) — мягкая проверка.

    Запасная — любая площадка, кроме площадки посадки вылета: резервная (она
    для внештатных случаев, №41) или другая штатная. Площадка посадки может
    стать недоступной — тогда борт садится на ближайшую запасную.
    Из каждой точки маршрута — прямой перелёт до ближайшей запасной на
    путевой скорости перелёта этого вылета, снижение и посадка — против
    остатка заряда всей батареи: резерв 20% и есть запас на нештатный случай.
    Прямая зон не обходит — это оценка досягаемости, а не маршрут. Точки
    маршрута — вершины участков: дефицит вдоль отрезка выпуклый (расстояние
    до точки выпукло, остаток заряда линеен по времени), наибольший — в
    вершине.

    Не нарушение: заказчик резервных площадок не требует, план без запасной
    выполним. Предупреждение — одно на модель борта, с худшей точкой.

    Returns:
        Предупреждения `validate.alternate_reach` и, если запасных площадок
        нет ни у одного вылета, справка `validate.no_alternate`.
    """
    worst: dict[str, _AlternateGap] = {}
    affected: dict[str, set[str]] = {}
    flown: dict[str, int] = {}
    lonely = 0
    for sortie in sorties:
        vehicle = vehicles[sortie.vehicle_id]
        flown[vehicle.id] = flown.get(vehicle.id, 0) + 1
        alternates = _alternates(scenario, work_area, sortie.landing_site_id)
        if not alternates:
            lonely += 1
            continue
        speeds = [
            leg.ground_speed_mps
            for leg in sortie.legs
            if leg.phase is FlightPhase.TRANSIT and leg.ground_speed_mps > 0.0
        ]
        speed_mps = max(speeds) if speeds else vehicle.cruise_speed_mps
        points = np.array(
            [
                (waypoint.x_m, waypoint.y_m, waypoint.alt_m, waypoint.time_s)
                for leg in _airborne_legs(sortie)
                for waypoint in leg.waypoints
            ]
        )
        if not len(points):
            continue
        site_ids = list(alternates)
        sites = np.array([(alternates[key].x_m, alternates[key].y_m) for key in site_ids])
        distances = np.hypot(
            points[:, None, 0] - sites[None, :, 0], points[:, None, 1] - sites[None, :, 1]
        )
        nearest = distances.argmin(axis=1)
        distance_m = distances[np.arange(len(points)), nearest]
        spare_s = (
            vehicle.endurance_s
            - (points[:, 3] - sortie.start_time_s)
            - points[:, 2] / assumptions.DESCENT_RATE_MPS
            - assumptions.LANDING_TIME_S
        )
        reach_m = np.maximum(spare_s, 0.0) * speed_mps
        deficit = distance_m - reach_m
        index = int(deficit.argmax())
        if deficit[index] <= 0.0:
            continue
        affected.setdefault(vehicle.id, set()).add(sortie.id)
        gap = worst.get(vehicle.id)
        if gap is None or deficit[index] > gap.distance_m - gap.reach_m:
            site_id = site_ids[int(nearest[index])]
            worst[vehicle.id] = _AlternateGap(
                sortie_id=sortie.id,
                sortie_label=text.sortie_label(sortie, _names(vehicles)),
                point=MetricPoint(x_m=float(points[index, 0]), y_m=float(points[index, 1])),
                site=alternates[site_id],
                site_id=site_id,
                distance_m=float(distance_m[index]),
                reach_m=float(reach_m[index]),
            )

    site_names = {site.id: site.name for site in scenario.sites if site.name}
    items: list[PlanWarning] = []
    for vehicle_id, gap in sorted(worst.items()):
        vehicle = vehicles[vehicle_id]
        items.append(
            PlanWarning(
                code="validate.alternate_reach",
                severity=Severity.WARNING,
                stage=Stage.VALIDATE,
                message=(
                    f"{vehicle.name}: в {len(affected[vehicle_id])} из {flown[vehicle_id]} "
                    "вылетов есть точки, откуда запасная площадка недосягаема. Худшая — "
                    f"{gap.sortie_label}: до площадки {text.site_label(gap.site_id, site_names)} "
                    f"по прямой {text.num(gap.distance_m / 1000, 2)} км, остатка заряда хватит "
                    f"на {text.num(gap.reach_m / 1000, 2)} км. Если площадка посадки станет "
                    "недоступна, из этих точек борту садиться некуда"
                ),
                context={
                    "vehicle_id": vehicle_id,
                    "sortie_id": gap.sortie_id,
                    "site_id": gap.site_id,
                    "sorties": str(len(affected[vehicle_id])),
                },
                geometry=MetricLineString(points=[gap.point, gap.site]),
            )
        )
    if lonely and lonely == len(sorties):
        items.append(
            PlanWarning(
                code="validate.no_alternate",
                severity=Severity.INFO,
                stage=Stage.VALIDATE,
                message=(
                    "Запасной площадки нет: в сценарии одна площадка — если она станет "
                    "недоступна, борту садиться некуда"
                ),
            )
        )
    return items


def _derived_value(vehicle: Vehicle, key: str) -> str | None:
    """Выведенное число модели словами; отсечка заряда — общая норма, не ТТХ модели."""
    if key == "battery_reserve_fraction":
        return None
    if key == "cruise_speed_mps":
        return f"крейсерская скорость {text.num(vehicle.cruise_speed_mps)} м/с"
    if key == "turn_radius_m":
        return f"радиус разворота {vehicle.turn_radius_m:.0f} м"
    return text.field_label(key)


def _derived_specs(vehicles: dict[str, Vehicle], sorties: list[Sortie]) -> list[PlanWarning]:
    """Какие числа плана выведены правилом, а не взяты из ТТХ (инвариант 6)."""
    items = []
    for vehicle_id in sorted({sortie.vehicle_id for sortie in sorties}):
        vehicle = vehicles[vehicle_id]
        values = [
            value
            for value in (_derived_value(vehicle, key) for key in vehicle.derived_fields)
            if value is not None
        ]
        if not values:
            continue
        listing = values[0] if len(values) == 1 else f"{', '.join(values[:-1])} и {values[-1]}"
        computed = "значение рассчитано" if len(values) == 1 else "значения рассчитаны"
        items.append(
            PlanWarning(
                code="validate.derived_specs",
                severity=Severity.INFO,
                stage=Stage.VALIDATE,
                message=(
                    f"{vehicle.name}: {listing} — в документации производителя этого нет, "
                    f"{computed} из паспортных характеристик модели"
                ),
                context={"vehicle_id": vehicle_id},
            )
        )
    return items


def validate_plan(
    scenario: Scenario,
    work_area: WorkArea,
    decomposition: Decomposition,
    coverage: CoverageSet | None,
    sorties: list[Sortie],
    vehicles: dict[str, Vehicle],
) -> ValidationReport:
    """Проверить готовый план по жёстким правилам и мягким критериям.

    Args:
        scenario: исходный сценарий.
        work_area: рабочая область (зоны, препятствия, пространство, коридор).
        decomposition: подобласти — для проверки полноты.
        coverage: покрытие (зоны манёвра).
        sorties: вылеты на общей оси времени.
        vehicles: каталог бортов.

    Returns:
        Нарушения и предупреждения. Пустой список нарушений — план выполним.
    """
    names = _names(vehicles)
    sites = {site.id: site.name for site in scenario.sites if site.name}
    report = ValidationReport()
    report.violations.extend(_check_coverage(decomposition, sorties))
    report.violations.extend(_check_energy(sorties, vehicles))
    report.violations.extend(_check_range(sorties, vehicles))
    wind_violations, wind_warnings = _check_wind(scenario, sorties, vehicles)
    report.violations.extend(wind_violations)
    report.warnings.extend(wind_warnings)
    report.violations.extend(_check_zones(work_area, sorties, coverage, names))
    report.violations.extend(_check_obstacles(work_area, sorties, coverage, names))
    report.violations.extend(_check_airspace(work_area, sorties, coverage, names))
    report.violations.extend(_check_altitudes(work_area, sorties, names))
    report.violations.extend(_check_separation(sorties, vehicles))
    report.violations.extend(_check_maneuver_separation(sorties, coverage, vehicles))
    report.violations.extend(_check_continuity(sorties, names, sites))
    report.violations.extend(_check_deadline(scenario, sorties))
    report.violations.extend(_check_daylight(scenario, work_area, sorties, names))
    report.warnings.extend(_check_alternate_reach(scenario, work_area, sorties, vehicles))
    report.warnings.extend(_derived_specs(vehicles, sorties))
    report.warnings.append(
        PlanWarning(
            code="validate.not_checked",
            severity=Severity.INFO,
            stage=Stage.VALIDATE,
            message=(
                "Не проверяются: форма петли разворота — вместо неё проверяется вся защитная "
                "зона манёвра; рельеф — высоты отсчитываются от земли без цифровой модели "
                "рельефа"
            ),
        )
    )
    return report


__all__ = ["ValidationReport", "validate_plan"]
