"""Независимый краш-тест готового плана (E7, P0).

Заказчик проверяет решения краш-тестом (№4): нет столкновений и опасных
сближений, нет залёта в запретные зоны, нет опасных пролётов у высотных
препятствий. Валидатор — наш собственный краш-тест. Он НЕ доверяет стадиям,
которые строили план: берёт готовые вылеты с временами и проверяет их заново.

Жёсткие правила (инвариант 9) дают НАРУШЕНИЯ: план с ними не выдаётся, задание
объявляется невыполнимым с причиной. Мягкие — предупреждения.

Не проверяется (объявлено в отчёте): досягаемость резервной площадки из любой
точки маршрута, траектория петли разворота (вместо неё — защитная зона
манёвра), рельеф между препятствиями (нет ЦМР). TODO(E7).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from core import assumptions
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


def _check_energy(sorties: list[Sortie], vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Заряд: линейная модель, отсечка 20% (№34, №35)."""
    items = []
    for sortie in sorties:
        feasible, reason = sortie_is_feasible(sortie, vehicles[sortie.vehicle_id])
        if not feasible:
            items.append(
                _violation(
                    "validate.energy",
                    f"Вылет {sortie.id}: {reason}",
                    sortie_id=sortie.id,
                    vehicle_id=sortie.vehicle_id,
                )
            )
    return items


def _check_range(sorties: list[Sortie], vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Протяжённость маршрута против паспортной для модели БЛА (долг №43).

    Первая проверка, которую делает над полётным заданием сам GeoScan Planner
    заказчика: «Общая длина маршрута превышает допустимую для данной модели
    БЛА» (docs/planner_reference.md §6). Запас хода — не то же самое, что
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
                f"Вылет {sortie.id}: маршрут {sortie.distance_m / 1000:.1f} км длиннее "
                f"паспортной протяжённости {vehicle.max_range_m / 1000:.0f} км "
                f"для {vehicle.name}",
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
                    f"{vehicle.name}: ветер {wind.speed_mps:.1f} м/с выше паспортного предела "
                    f"{vehicle.max_wind_mps:.1f} м/с",
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
                        f"{vehicle.name}: ветер {wind.speed_mps:.1f} м/с выше "
                        f"{assumptions.WIND_SAFETY_FRACTION * 100:.0f}% паспортного предела — "
                        "запаса на порывы почти нет"
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
                        f"Вылет {sortie.id}: на галсе курсом {course:.0f}° нужна воздушная "
                        f"скорость {needed:.1f} м/с — вне диапазона борта",
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
    work_area: WorkArea, sorties: list[Sortie], coverage: CoverageSet | None
) -> list[PlanWarning]:
    """Запретные зоны в плане, по высоте и во времени (№40), с буфером."""
    items: list[PlanWarning] = []
    for sortie in sorties:
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
                            f"Вылет {sortie.id}: участок «{leg.phase.value}» входит в "
                            f"запретную зону {zone.id!r} (с буфером "
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
                            f"Вылет {sortie.id}: зона манёвра самолёта в подобласти "
                            f"{window.cell_id} задевает запретную зону {zone.id!r}",
                            sortie_id=sortie.id,
                            zone_id=zone.id,
                            cell_id=window.cell_id,
                        )
                    )
    return items


def _check_obstacles(
    work_area: WorkArea, sorties: list[Sortie], coverage: CoverageSet | None
) -> list[PlanWarning]:
    """Высотные препятствия: не ближе буфера в плане, если высота ниже безопасной (№4)."""
    items: list[PlanWarning] = []
    if not work_area.obstacles:
        return items
    for sortie in sorties:
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
                            f"Вылет {sortie.id}: участок «{leg.phase.value}» на высоте "
                            f"{low:.0f} м проходит ближе {assumptions.OBSTACLE_BUFFER_M:.0f} м "
                            f"от препятствия {obstacle.id!r} высотой {obstacle.height_m:.0f} м "
                            f"(безопасно выше {obstacle.safe_altitude_m:.0f} м)",
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
                            f"Вылет {sortie.id}: зона манёвра самолёта в подобласти "
                            f"{window.cell_id} задевает препятствие {obstacle.id!r}",
                            sortie_id=sortie.id,
                            obstacle_id=obstacle.id,
                            cell_id=window.cell_id,
                        )
                    )
    return items


def _check_airspace(
    work_area: WorkArea, sorties: list[Sortie], coverage: CoverageSet | None
) -> list[PlanWarning]:
    """Граница разрешённого воздушного пространства."""
    if work_area.airspace is None:
        return []
    allowed = work_area.airspace.buffer(assumptions.BOUNDARY_TOLERANCE_M)
    items: list[PlanWarning] = []
    for sortie in sorties:
        for leg in _airborne_legs(sortie):
            if not _leg_shape(leg).within(allowed):
                items.append(
                    _violation(
                        "validate.airspace",
                        f"Вылет {sortie.id}: участок «{leg.phase.value}» выходит за границу "
                        "разрешённого воздушного пространства",
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
                        f"Вылет {sortie.id}: зона манёвра в подобласти {window.cell_id} выходит "
                        "за границу разрешённого воздушного пространства",
                        sortie_id=sortie.id,
                        cell_id=window.cell_id,
                    )
                )
                break
    return items


def _check_altitudes(work_area: WorkArea, sorties: list[Sortie]) -> list[PlanWarning]:
    """Коридор высот и одна высота съёмки на вылет (№10)."""
    items: list[PlanWarning] = []
    for sortie in sorties:
        top = max(waypoint.alt_m for leg in sortie.legs for waypoint in leg.waypoints)
        if top > work_area.corridor_max_m + assumptions.BOUNDARY_TOLERANCE_M:
            items.append(
                _violation(
                    "validate.corridor",
                    f"Вылет {sortie.id}: высота {top:.0f} м выше коридора "
                    f"{work_area.corridor_max_m:.0f} м",
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
                    f"Вылет {sortie.id}: съёмка ведётся на разных высотах "
                    f"{sorted(survey)} — полезная работа должна идти на одной",
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
                    f"Сближение: {first.sortie_id} и {second.sortie_id} на "
                    f"{conflict.time_s / 60:.1f} мин от начала работ — "
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
                    f"Зоны манёвра сходятся: {first.sortie_id} (подобласть {first.cell_id}) и "
                    f"{second.sortie_id} (подобласть {second.cell_id}) заняты вместе с "
                    f"{overlap.from_s / 60:.1f} по {overlap.to_s / 60:.1f} мин от начала работ "
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
    return [
        _violation(
            "validate.coverage_incomplete",
            f"{len(missing)} из {len(decomposition.cells)} подобластей не сняты ни одним "
            "вылетом — задание не выполнено целиком",
            cell_ids=",".join(missing),
        )
    ]


def _check_continuity(sorties: list[Sortie]) -> list[PlanWarning]:
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
            if current.takeoff_site_id != previous.landing_site_id:
                items.append(
                    _violation(
                        "validate.site_continuity",
                        f"Вылет {current.id} начинается с площадки {current.takeoff_site_id!r}, "
                        f"а предыдущий вылет борта закончился на {previous.landing_site_id!r}",
                        sortie_id=current.id,
                    )
                )
            if current.start_time_s < previous.end_time_s():
                items.append(
                    _violation(
                        "validate.vehicle_overlap",
                        f"Вылет {current.id} начинается раньше, чем борт сел после {previous.id}",
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
            f"Работы занимают {makespan / 3600:.1f} ч при ограничении "
            f"{scenario.deadline_s / 3600:.1f} ч",
        )
    ]


def _check_daylight(
    scenario: Scenario, work_area: WorkArea, sorties: list[Sortie]
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
                        f"Вылет {sortie.id}: галс снимается при Солнце {low:.1f}° — ниже "
                        f"заданных {threshold:.0f}°",
                        _leg_line(leg),
                        sortie_id=sortie.id,
                        strip_id=leg.strip_id or "",
                    )
                )
                break
    return items


def _derived_specs(vehicles: dict[str, Vehicle], sorties: list[Sortie]) -> list[PlanWarning]:
    """Какие числа плана выведены допущением, а не взяты из ТТХ (инвариант 6)."""
    items = []
    for vehicle_id in sorted({sortie.vehicle_id for sortie in sorties}):
        vehicle = vehicles[vehicle_id]
        if not vehicle.derived_fields:
            continue
        items.append(
            PlanWarning(
                code="validate.derived_specs",
                severity=Severity.INFO,
                stage=Stage.VALIDATE,
                message=(
                    f"{vehicle.name}: поля {', '.join(vehicle.derived_fields)} выведены "
                    "допущениями (core/assumptions.py), в официальном документе их нет"
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
    report = ValidationReport()
    report.violations.extend(_check_coverage(decomposition, sorties))
    report.violations.extend(_check_energy(sorties, vehicles))
    report.violations.extend(_check_range(sorties, vehicles))
    wind_violations, wind_warnings = _check_wind(scenario, sorties, vehicles)
    report.violations.extend(wind_violations)
    report.warnings.extend(wind_warnings)
    report.violations.extend(_check_zones(work_area, sorties, coverage))
    report.violations.extend(_check_obstacles(work_area, sorties, coverage))
    report.violations.extend(_check_airspace(work_area, sorties, coverage))
    report.violations.extend(_check_altitudes(work_area, sorties))
    report.violations.extend(_check_separation(sorties, vehicles))
    report.violations.extend(_check_maneuver_separation(sorties, coverage, vehicles))
    report.violations.extend(_check_continuity(sorties))
    report.violations.extend(_check_deadline(scenario, sorties))
    report.violations.extend(_check_daylight(scenario, work_area, sorties))
    report.warnings.extend(_derived_specs(vehicles, sorties))
    report.warnings.append(
        PlanWarning(
            code="validate.not_checked",
            severity=Severity.INFO,
            stage=Stage.VALIDATE,
            message=(
                "Не проверяются: досягаемость резервной площадки из любой точки маршрута, "
                "траектория петли разворота (проверена защитная зона манёвра), рельеф "
                "между препятствиями — ЦМР не подключена (TODO(E7))"
            ),
        )
    )
    return report


__all__ = ["ValidationReport", "validate_plan"]
