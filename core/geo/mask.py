"""Рабочая область: сценарий в метрах, зоны, препятствия, коридор, маска съёмки.

Вся геометрия здесь — метры (инвариант 1).

Запретные зоны — пространственно-временные (заказчик, №40): полигон, высотный
диапазон над поверхностью и окно действия. Высотные препятствия — отдельный
слой (№2). Маска съёмки — область, из которой вычтено то, что мешает снимать
на высоте съёмки: постоянные зоны этого высотного диапазона и препятствия
выше высоты съёмки за вычетом запаса. Временные зоны из маски не вычитаются:
под ними снимать надо, но вне окна действия — это забота расписания (E14).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.errors import GeometryError, ScenarioError
from core.geo.crs import pick_working_crs
from core.geo.project import Projector, polygons_of
from core.geo.sun import daylight_windows
from core.schemas import (
    CrsInfo,
    MetricPoint,
    NoFlyZone,
    PlanWarning,
    Scenario,
    Severity,
    Stage,
)

_M2_PER_KM2 = 1.0e6


def as_utc(moment: datetime) -> datetime:
    """Момент времени с часовым поясом; время без пояса считается UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ZoneArea:
    """Запретная зона в метрах, с высотами и окном действия."""

    id: str
    name: str
    footprint: BaseGeometry
    """Полигон зоны, метры."""
    buffered: BaseGeometry
    """Полигон с буфером безопасности `NFZ_BUFFER_M`, метры."""
    inner: BaseGeometry
    """Буфер, уменьшенный на допуск касания — для проверок пересечения."""
    min_alt_m: float
    max_alt_m: float
    active_from_s: float
    """Начало действия от начала работ, секунды; −∞ — действует всегда."""
    active_to_s: float
    """Конец действия от начала работ, секунды; +∞ — действует всегда."""

    @property
    def permanent(self) -> bool:
        """True — зона без окна действия."""
        return math.isinf(self.active_from_s) and math.isinf(self.active_to_s)

    def blocks_altitude(self, low_m: float, high_m: float) -> bool:
        """Запрещает ли зона полёт в диапазоне высот [low, high], метры AGL."""
        return low_m < self.max_alt_m and high_m > self.min_alt_m

    def active_during(self, start_s: float, end_s: float) -> bool:
        """Действует ли зона хотя бы часть интервала [start, end], секунды."""
        return start_s < self.active_to_s and end_s > self.active_from_s


@dataclass(frozen=True)
class ObstacleArea:
    """Высотное препятствие в метрах."""

    id: str
    name: str
    footprint: BaseGeometry
    """Точка или полигон препятствия, метры."""
    buffered: BaseGeometry
    """Препятствие с горизонтальным буфером `OBSTACLE_BUFFER_M`, метры."""
    inner: BaseGeometry
    """Буфер, уменьшенный на допуск касания — для проверок пересечения."""
    height_m: float

    @property
    def safe_altitude_m(self) -> float:
        """Высота, выше которой пролёт над препятствием безопасен, м AGL."""
        return self.height_m + assumptions.OBSTACLE_VERTICAL_CLEARANCE_M

    def blocks_altitude(self, low_m: float) -> bool:
        """Мешает ли препятствие полёту на высоте от `low_m` и выше."""
        return low_m < self.safe_altitude_m


@dataclass
class WorkArea:
    """Геометрия сценария, переведённая в метры."""

    crs: CrsInfo
    projector: Projector
    area: BaseGeometry
    """Исходная область съёмки (полигон или мультиполигон), метры."""
    airspace: BaseGeometry | None
    """Граница разрешённого воздушного пространства, метры. None — не задана."""
    zones: list[ZoneArea]
    obstacles: list[ObstacleArea]
    corridor_max_m: float
    """Верхняя граница коридора высот, м AGL."""
    sites: dict[str, MetricPoint] = field(default_factory=dict)
    """Площадки взлёта-посадки в метрах, по идентификаторам."""
    warnings: list[PlanWarning] = field(default_factory=list)
    daylight: list[tuple[float, float]] | None = None
    """Окна съёмки по высоте Солнца (`Scenario.min_sun_elevation_deg`), секунды от
    начала работ, по порядку; None — ограничения нет."""

    @property
    def area_m2(self) -> float:
        """Площадь исходной области съёмки, м²."""
        return float(self.area.area)

    def site(self, site_id: str) -> MetricPoint:
        """Площадка по идентификатору, метры.

        Raises:
            GeometryError: площадки нет в сценарии.
        """
        try:
            return self.sites[site_id]
        except KeyError as exc:
            raise GeometryError(f"Площадка {site_id!r} отсутствует в сценарии") from exc

    def zones_blocking(
        self, low_m: float, high_m: float, permanent_only: bool, inner: bool = False
    ) -> BaseGeometry:
        """Объединение забуференных зон, запрещающих высоты [low, high].

        Args:
            low_m: нижняя граница диапазона высот, м AGL.
            high_m: верхняя граница диапазона высот, м AGL.
            permanent_only: True — только зоны без окна действия.
            inner: True — буфер за вычетом допуска касания (для проверок).

        Returns:
            Геометрия в метрах; пустая, если таких зон нет.
        """
        parts = [
            zone.inner if inner else zone.buffered
            for zone in self.zones
            if zone.blocks_altitude(low_m, high_m) and (zone.permanent or not permanent_only)
        ]
        return unary_union(parts) if parts else Polygon()

    def obstacles_blocking(self, low_m: float, inner: bool = False) -> BaseGeometry:
        """Объединение забуференных препятствий, мешающих полёту на высоте `low_m`."""
        parts = [
            item.inner if inner else item.buffered
            for item in self.obstacles
            if item.blocks_altitude(low_m)
        ]
        return unary_union(parts) if parts else Polygon()


def _as_polygonal(geometry: BaseGeometry, what: str) -> BaseGeometry:
    """Оставить от геометрии только полигональную часть.

    Разность полигонов может дать коллекцию с обрывками линий на общих
    границах — такие обрывки не имеют площади и мешают дальнейшим операциям.
    """
    parts = polygons_of(geometry)
    if not parts:
        raise GeometryError(f"{what}: не осталось площади")
    return MultiPolygon(parts) if len(parts) > 1 else parts[0]


def _zone_window_s(
    zone: NoFlyZone, start_time: datetime | None, warnings: list[PlanWarning]
) -> tuple[float, float]:
    """Окно действия зоны в секундах от начала работ.

    Без времени начала работ окно не с чем сопоставить. Тогда зона считается
    действующей всегда — это консервативно и безопасно, но пользователь
    предупреждается.
    """
    if zone.is_permanent():
        return -math.inf, math.inf
    if start_time is None:
        warnings.append(
            PlanWarning(
                code="geo.temporal_zone_without_start",
                severity=Severity.WARNING,
                stage=Stage.GEO,
                message=(
                    f"Зона {zone.id!r} временная, но время начала работ не задано — "
                    "зона считается действующей всегда. Укажите start_time сценария"
                ),
                context={"zone_id": zone.id},
            )
        )
        return -math.inf, math.inf
    origin = as_utc(start_time)
    start = (
        -math.inf
        if zone.active_from is None
        else (as_utc(zone.active_from) - origin).total_seconds()
    )
    end = math.inf if zone.active_to is None else (as_utc(zone.active_to) - origin).total_seconds()
    return start, end


def build_work_area(scenario: Scenario) -> WorkArea:
    """Перевести сценарий в метры и проверить геометрию входа.

    Args:
        scenario: сценарий с координатами в WGS84.

    Returns:
        Рабочая область: область, зоны, препятствия, коридор, площадки.

    Raises:
        GeometryError: геометрия некорректна (самопересечение и т.п.) —
            с указанием места.
        ScenarioError: область больше допустимой (заказчик, №19).
    """
    anchor_points = [point for polygon in scenario.area.polygons for point in polygon.outer]
    anchor_points.extend(site.position for site in scenario.sites)
    for zone in scenario.no_fly_zones:
        anchor_points.extend(zone.polygon.outer)

    crs, _origin = pick_working_crs(anchor_points)
    projector = Projector(crs)
    warnings: list[PlanWarning] = []

    area = projector.multipolygon_to_metric(scenario.area, "Область съёмки")
    area_km2 = area.area / _M2_PER_KM2
    if area_km2 > assumptions.MAX_SURVEY_AREA_KM2:
        raise ScenarioError(
            f"Область съёмки {area_km2:.0f} км² больше допустимых "
            f"{assumptions.MAX_SURVEY_AREA_KM2:.0f} км². Проверьте единицы и координаты"
        )

    zones: list[ZoneArea] = []
    for zone in scenario.no_fly_zones:
        footprint = projector.polygon_to_metric(zone.polygon, f"Запретная зона {zone.id!r}")
        active_from_s, active_to_s = _zone_window_s(zone, scenario.start_time, warnings)
        buffered = footprint.buffer(assumptions.NFZ_BUFFER_M)
        zones.append(
            ZoneArea(
                id=zone.id,
                name=zone.name,
                footprint=footprint,
                buffered=buffered,
                inner=buffered.buffer(-assumptions.BOUNDARY_TOLERANCE_M),
                min_alt_m=zone.min_alt_m if zone.min_alt_m is not None else 0.0,
                max_alt_m=zone.max_alt_m if zone.max_alt_m is not None else math.inf,
                active_from_s=active_from_s,
                active_to_s=active_to_s,
            )
        )

    obstacles: list[ObstacleArea] = []
    for obstacle in scenario.obstacles:
        if obstacle.polygon is not None:
            footprint: BaseGeometry = projector.polygon_to_metric(
                obstacle.polygon, f"Препятствие {obstacle.id!r}"
            )
        else:
            assert obstacle.position is not None
            point = projector.to_metric(obstacle.position)
            footprint = Point(point.x_m, point.y_m)
        buffered = footprint.buffer(assumptions.OBSTACLE_BUFFER_M)
        obstacles.append(
            ObstacleArea(
                id=obstacle.id,
                name=obstacle.name,
                footprint=footprint,
                buffered=buffered,
                inner=buffered.buffer(-assumptions.BOUNDARY_TOLERANCE_M),
                height_m=obstacle.height_m,
            )
        )

    airspace = (
        projector.polygon_to_metric(scenario.airspace_boundary, "Граница воздушного пространства")
        if scenario.airspace_boundary is not None
        else None
    )

    sites: dict[str, MetricPoint] = {}
    for site in scenario.sites:
        point = projector.to_metric(site.position)
        sites[site.id] = point
        warnings.extend(_check_site(site.id, point, zones, airspace))

    daylight: list[tuple[float, float]] | None = None
    if scenario.min_sun_elevation_deg is not None and scenario.start_time is not None:
        # Окна — по центру области: на 30 км высота Солнца меняется меньше
        # чем на 0,3°; краш-тест проверяет каждый галс в его точке.
        center = projector.xy_to_geo(area.centroid.x, area.centroid.y)
        daylight = daylight_windows(
            as_utc(scenario.start_time), center.lat, center.lon, scenario.min_sun_elevation_deg
        )
        warnings.append(_daylight_note(scenario.min_sun_elevation_deg, daylight))

    return WorkArea(
        crs=crs,
        projector=projector,
        area=area,
        airspace=airspace,
        zones=zones,
        obstacles=obstacles,
        corridor_max_m=scenario.altitude_corridor.max_altitude_agl_m,
        sites=sites,
        warnings=warnings,
        daylight=daylight,
    )


def _daylight_note(threshold_deg: float, windows: list[tuple[float, float]]) -> PlanWarning:
    """Предупреждение с окнами съёмки по Солнцу: первые сутки и сколько их всего."""
    hours = sum(end - start for start, end in windows) / 3600.0
    first = (
        f"первое — {windows[0][0] / 3600.0:.1f}–{windows[0][1] / 3600.0:.1f} ч от начала работ"
        if windows
        else "окон нет"
    )
    return PlanWarning(
        code="geo.daylight",
        severity=Severity.INFO if windows else Severity.WARNING,
        stage=Stage.GEO,
        message=(
            f"Съёмка — только при Солнце не ниже {threshold_deg:.0f}°: "
            f"{len(windows)} окон за {assumptions.DAYLIGHT_HORIZON_DAYS} суток, "
            f"всего {hours:.1f} ч; {first}. Галсы — внутри окон, перелёты — в любое время"
        ),
        context={"windows": str(len(windows))},
    )


def build_survey_mask(
    work_area: WorkArea, low_alt_m: float, high_alt_m: float
) -> tuple[BaseGeometry, list[PlanWarning]]:
    """Маска съёмки: где можно снимать на высотах [low, high].

    Порядок: область → минус постоянные зоны этого диапазона высот → минус
    препятствия выше `low` с запасом → пересечение с разрешённым пространством.

    Args:
        work_area: рабочая область в метрах.
        low_alt_m: нижняя высота съёмки флота, м AGL.
        high_alt_m: верхняя высота съёмки флота, м AGL.

    Returns:
        Пара «маска в метрах, предупреждения».

    Raises:
        GeometryError: снимать негде — маска пуста.
    """
    warnings: list[PlanWarning] = []
    mask: BaseGeometry = work_area.area

    zones = work_area.zones_blocking(low_alt_m, high_alt_m, permanent_only=True)
    if not zones.is_empty:
        mask = _as_polygonal(mask.difference(zones), "Запретные зоны накрывают область съёмки")

    obstacles = work_area.obstacles_blocking(low_alt_m)
    if not obstacles.is_empty:
        mask = _as_polygonal(
            mask.difference(obstacles), "Высотные препятствия накрывают область съёмки"
        )

    if work_area.airspace is not None:
        mask = _as_polygonal(
            mask.intersection(work_area.airspace),
            "Граница воздушного пространства не пересекается с областью съёмки",
        )

    if mask.area <= assumptions.GEOMETRY_TOLERANCE_M**2:
        raise GeometryError("Маска съёмки пуста: снимать негде")

    lost_fraction = 1.0 - mask.area / work_area.area_m2 if work_area.area_m2 > 0 else 0.0
    if lost_fraction > 0.0:
        warnings.append(
            PlanWarning(
                code="geo.area_reduced",
                severity=Severity.INFO,
                stage=Stage.GEO,
                message=(
                    f"Запретные зоны, препятствия и граница воздушного пространства "
                    f"исключили из съёмки {lost_fraction * 100:.1f}% области (буфер вокруг "
                    f"зон {assumptions.NFZ_BUFFER_M:.0f} м, вокруг препятствий "
                    f"{assumptions.OBSTACLE_BUFFER_M:.0f} м)"
                ),
            )
        )
    return mask, warnings


def _check_site(
    site_id: str, point: MetricPoint, zones: list[ZoneArea], airspace: BaseGeometry | None
) -> list[PlanWarning]:
    """Предупредить, если площадка внутри запретной зоны или вне пространства."""
    here = Point(point.x_m, point.y_m)
    warnings: list[PlanWarning] = []
    for zone in zones:
        if zone.min_alt_m <= 0.0 and zone.buffered.contains(here):
            warnings.append(
                PlanWarning(
                    code="geo.site_in_nfz",
                    severity=Severity.ERROR,
                    stage=Stage.GEO,
                    message=(
                        f"Площадка {site_id!r} внутри запретной зоны {zone.id!r} (с учётом "
                        "буфера) — взлёт и посадка с неё нарушат зону"
                    ),
                    context={"site_id": site_id, "zone_id": zone.id},
                )
            )
    if airspace is not None and not airspace.contains(here):
        warnings.append(
            PlanWarning(
                code="geo.site_outside_airspace",
                severity=Severity.ERROR,
                stage=Stage.GEO,
                message=f"Площадка {site_id!r} вне разрешённого воздушного пространства",
                context={"site_id": site_id},
            )
        )
    return warnings


__all__ = [
    "ObstacleArea",
    "WorkArea",
    "ZoneArea",
    "as_utc",
    "build_survey_mask",
    "build_work_area",
]
