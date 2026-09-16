"""Рабочая маска области: где вообще разрешено летать и снимать.

Маска — это область съёмки, из которой вычтены забуференные запретные зоны и
которая обрезана по границе разрешённого воздушного пространства. Дальше по
конвейеру галсы строятся только внутри маски.

Вся геометрия здесь — метры (инвариант 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.errors import GeometryError
from core.geo.project import Projector
from core.schemas import CrsInfo, MetricPoint, PlanWarning, Scenario, Severity, Site, Stage


@dataclass
class WorkArea:
    """Геометрия сценария, переведённая в метры и подготовленная к покрытию."""

    crs: CrsInfo
    projector: Projector
    area: BaseGeometry
    """Исходная область съёмки, метры."""
    mask: BaseGeometry
    """Рабочая маска: область минус запретные зоны, обрезанная по воздушному пространству."""
    no_fly: BaseGeometry
    """Объединение забуференных запретных зон, метры. Пустая геометрия, если зон нет."""
    sites: dict[str, MetricPoint] = field(default_factory=dict)
    """Площадки взлёта-посадки в метрах, по идентификаторам."""
    warnings: list[PlanWarning] = field(default_factory=list)

    @property
    def area_m2(self) -> float:
        """Площадь исходной области съёмки, м²."""
        return float(self.area.area)

    @property
    def mask_m2(self) -> float:
        """Площадь рабочей маски, м²."""
        return float(self.mask.area)

    def site(self, site_id: str) -> MetricPoint:
        """Площадка по идентификатору, метры.

        Raises:
            GeometryError: площадки нет в сценарии.
        """
        try:
            return self.sites[site_id]
        except KeyError as exc:
            raise GeometryError(f"Площадка {site_id!r} отсутствует в сценарии") from exc


def _as_polygonal(geometry: BaseGeometry, what: str) -> BaseGeometry:
    """Оставить от геометрии только полигональную часть.

    Разность полигонов может дать коллекцию с обрывками линий на общих
    границах — такие обрывки не имеют площади и мешают дальнейшим операциям.
    """
    if geometry.is_empty:
        raise GeometryError(f"{what}: после вычитания не осталось площади")
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    parts = [geom for geom in getattr(geometry, "geoms", []) if isinstance(geom, Polygon)]
    if not parts:
        raise GeometryError(f"{what}: результат не содержит полигонов")
    return MultiPolygon(parts) if len(parts) > 1 else parts[0]


def build_work_area(scenario: Scenario, buffer_m: float | None = None) -> WorkArea:
    """Перевести сценарий в метры и построить рабочую маску.

    Порядок операций: проекция → буфер вокруг запретных зон → вычитание →
    обрезка по воздушному пространству.

    TODO(E1): высотные границы запретных зон (`min_alt_m` / `max_alt_m`)
    игнорируются — зона считается запретной на всех высотах. Это
    консервативно и потому безопасно, но отрезает допустимые пролёты сверху.

    Args:
        scenario: сценарий с координатами в WGS84.
        buffer_m: буфер вокруг запретных зон, метры. None — значение из
            `assumptions.NFZ_BUFFER_M`.

    Returns:
        Подготовленная рабочая область.

    Raises:
        GeometryError: область вырождена либо рабочая маска пуста.
    """
    buffer = assumptions.NFZ_BUFFER_M if buffer_m is None else buffer_m

    anchor_points = list(scenario.area.outer)
    anchor_points.extend(site.position for site in scenario.sites)
    for zone in scenario.no_fly_zones:
        anchor_points.extend(zone.polygon.outer)

    from core.geo.crs import pick_working_crs  # локальный импорт: избегаем цикла

    crs, _origin = pick_working_crs(anchor_points)
    projector = Projector(crs)

    area = projector.polygon_to_metric(scenario.area)
    warnings: list[PlanWarning] = []

    zones = [projector.polygon_to_metric(zone.polygon) for zone in scenario.no_fly_zones]
    no_fly: BaseGeometry = (
        unary_union([zone.buffer(buffer) for zone in zones]) if zones else Polygon()
    )

    mask: BaseGeometry = area
    if not no_fly.is_empty:
        mask = _as_polygonal(mask.difference(no_fly), "Запретные зоны накрывают область съёмки")

    if scenario.airspace_boundary is not None:
        airspace = projector.polygon_to_metric(scenario.airspace_boundary)
        mask = _as_polygonal(
            mask.intersection(airspace),
            "Граница воздушного пространства не пересекается с областью съёмки",
        )

    if mask.area <= assumptions.GEOMETRY_TOLERANCE_M**2:
        raise GeometryError("Рабочая маска пуста: снимать негде")

    lost_fraction = 1.0 - mask.area / area.area if area.area > 0 else 0.0
    if lost_fraction > 0.0:
        warnings.append(
            PlanWarning(
                code="geo.area_reduced",
                severity=Severity.INFO,
                stage=Stage.GEO,
                message=(
                    f"Запретные зоны и границы воздушного пространства отсекли "
                    f"{lost_fraction * 100:.1f}% площади съёмки "
                    f"(буфер вокруг запретных зон {buffer:.0f} м)"
                ),
            )
        )

    sites: dict[str, MetricPoint] = {}
    for site in scenario.sites:
        point = projector.to_metric(site.position)
        sites[site.id] = point
        warnings.extend(_check_site(site, point, no_fly))

    return WorkArea(
        crs=crs,
        projector=projector,
        area=area,
        mask=mask,
        no_fly=no_fly,
        sites=sites,
        warnings=warnings,
    )


def _check_site(site: Site, point: MetricPoint, no_fly: BaseGeometry) -> list[PlanWarning]:
    """Проверить, что площадка не оказалась внутри запретной зоны."""
    if no_fly.is_empty:
        return []
    if not no_fly.contains(Point(point.x_m, point.y_m)):
        return []
    return [
        PlanWarning(
            code="geo.site_in_nfz",
            severity=Severity.ERROR,
            stage=Stage.GEO,
            message=(
                f"Площадка {site.id!r} находится внутри запретной зоны "
                "(с учётом буфера безопасности) — взлёт с неё недопустим"
            ),
            context={"site_id": site.id},
        )
    ]


__all__ = ["WorkArea", "build_work_area"]
