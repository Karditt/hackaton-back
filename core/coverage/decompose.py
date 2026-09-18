"""Декомпозиция маски съёмки на подобласти — единицы распределения (E3).

Ядро задачи — разделение работ между бортами (заказчик, №46). Область не
режется одной сеткой галсов на весь флот: она делится на подобласти, они
распределяются по вылетам, и только потом каждый борт строит в своей
подобласти галсы со своим шагом (№27). Компактная подобласть на борт
разводит борта в плане естественным образом (№37, №38).

ЗАГЛУШКА E3: сетка квадратов в осях направления галсов, размер — по самому
«малоёмкому» борту. Не делается: трапецеидальная декомпозиция по форме
области, слияние мелких обрезков, учёт ветра при выборе направления.
"""

from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from core import assumptions
from core.coverage.strips import dominant_azimuth_deg
from core.geo.mask import WorkArea
from core.geo.project import polygon_from_shapely, polygons_of
from core.schemas import (
    Cell,
    Decomposition,
    MetricPoint,
    PlanWarning,
    Severity,
    Stage,
    SurveyPlan,
    Vehicle,
)


def sortie_capacity_m2(
    vehicle: Vehicle,
    survey_speed_mps: float,
    spacing_m: float,
    altitude_m: float,
    transit_s: float = 0.0,
) -> float:
    """Площадь, которую борт снимает за вылет, м².

    Грубая оценка: полётное время до отсечки заряда минус взлёт, посадка,
    набор, снижение и подлёт туда-обратно, умноженное на путевую скорость и
    шаг галсов.

    Args:
        vehicle: борт.
        survey_speed_mps: путевая скорость на галсе, м/с.
        spacing_m: шаг галсов, метры.
        altitude_m: высота съёмки, метры.
        transit_s: время подлёта и возврата, секунды.

    Returns:
        Площадь, м²; ноль, если на съёмку времени не остаётся.
    """
    usable_s = vehicle.endurance_s * (1.0 - vehicle.battery_reserve_fraction)
    overhead_s = (
        assumptions.TAKEOFF_TIME_S
        + assumptions.LANDING_TIME_S
        + altitude_m / assumptions.CLIMB_RATE_MPS
        + altitude_m / assumptions.DESCENT_RATE_MPS
        + transit_s
    )
    return max(usable_s - overhead_s, 0.0) * survey_speed_mps * spacing_m


def farthest_reach_m(mask: BaseGeometry, sites: list[MetricPoint]) -> float:
    """Самое большое расстояние от точки маски до ближайшей площадки, метры."""
    vertices = [
        (x, y)
        for part in polygons_of(mask)
        for ring in [part.exterior, *part.interiors]
        for x, y in ring.coords
    ]
    if not vertices or not sites:
        return 0.0
    return max(min(math.hypot(x - s.x_m, y - s.y_m) for s in sites) for x, y in vertices)


def cell_size_m(
    survey_plan: SurveyPlan,
    vehicles: dict[str, Vehicle],
    farthest_m: float = 0.0,
) -> float:
    """Сторона ячейки сетки декомпозиции, метры.

    Подобласть рассчитана на самый «малоёмкий» борт флота, который вообще
    долетает до самой дальней точки маски: её площадь — `CELL_FILL_FRACTION`
    от того, что он снимает за вылет после подлёта туда и обратно. Борт,
    которому на такой дистанции на съёмку не остаётся ничего, в расчёт размера
    не идёт — дальние подобласти возьмут другие. Сторона не меньше двух шагов
    галсов самого широкого борта — иначе в ячейку не ляжет змейка.

    Args:
        survey_plan: параметры съёмки пригодных бортов.
        vehicles: каталог бортов.
        farthest_m: расстояние от самой дальней точки маски до ближайшей площадки.
    """
    with_transit = []
    without_transit = []
    spacings = []
    for params in survey_plan.per_vehicle:
        vehicle = vehicles[params.vehicle_id]
        transit_s = 2.0 * farthest_m / params.transit_speed_mps
        args = (vehicle, params.survey_speed_mps, params.strip_spacing_m, params.altitude_agl_m)
        with_transit.append(sortie_capacity_m2(*args, transit_s=transit_s))
        without_transit.append(sortie_capacity_m2(*args))
        spacings.append(params.strip_spacing_m)
    reachable = [value for value in with_transit if value > 0.0]
    capacity = min(reachable) if reachable else min(without_transit)
    area_m2 = capacity * assumptions.CELL_FILL_FRACTION
    return max(math.sqrt(area_m2), 2.0 * max(spacings))


def decompose(
    work_area: WorkArea,
    mask: BaseGeometry,
    survey_plan: SurveyPlan,
    vehicles: dict[str, Vehicle],
    primary_site_ids: list[str],
) -> Decomposition:
    """Разрезать маску съёмки на подобласти.

    Args:
        work_area: рабочая область (проекция, площадки).
        mask: маска съёмки, метры.
        survey_plan: параметры съёмки пригодных бортов (не пустые).
        vehicles: каталог бортов.
        primary_site_ids: штатные площадки — от них считается дальность подлёта.

    Returns:
        Подобласти в порядке змейки по рядам сетки — так baseline режет их на
        связные куски.
    """
    azimuth = dominant_azimuth_deg(mask)
    sites = [work_area.site(site_id) for site_id in primary_site_ids]
    size_m = cell_size_m(survey_plan, vehicles, farthest_reach_m(mask, sites))

    # Сетка строится в осях галсов: маска поворачивается так, чтобы галсы
    # легли горизонтально, режется квадратами и возвращается обратно.
    math_angle_deg = 90.0 - azimuth
    pivot = mask.centroid
    rotated = affinity.rotate(mask, -math_angle_deg, origin=pivot, use_radians=False)
    prepared = prep(rotated)
    min_x, min_y, max_x, max_y = rotated.bounds
    columns = max(1, math.ceil((max_x - min_x) / size_m))
    rows = max(1, math.ceil((max_y - min_y) / size_m))

    cells: list[Cell] = []
    for row in range(rows):
        order = range(columns) if row % 2 == 0 else range(columns - 1, -1, -1)
        for col in order:
            square = box(
                min_x + col * size_m,
                min_y + row * size_m,
                min_x + (col + 1) * size_m,
                min_y + (row + 1) * size_m,
            )
            if not prepared.intersects(square):
                continue
            for part_index, part in enumerate(polygons_of(rotated.intersection(square))):
                if part.area <= assumptions.GEOMETRY_TOLERANCE_M**2:
                    continue
                restored = affinity.rotate(part, math_angle_deg, origin=pivot, use_radians=False)
                centroid = restored.centroid
                cells.append(
                    Cell(
                        id=f"c{row:03d}-{col:03d}-{part_index}",
                        polygon=polygon_from_shapely(restored),
                        area_m2=float(restored.area),
                        centroid=MetricPoint(x_m=centroid.x, y_m=centroid.y),
                        row=row,
                        col=col,
                    )
                )

    warnings = [
        PlanWarning(
            code="stub.decompose",
            severity=Severity.WARNING,
            stage=Stage.DECOMPOSE,
            message=(
                f"Подобласти — сетка квадратов со стороной {size_m:.0f} м в осях галсов "
                f"{azimuth:.0f}°. Направление взято по длинной стороне области, а не "
                "рассчитано с учётом ветра; мелкие обрезки по краям не сливаются (TODO(E3))"
            ),
        )
    ]
    return Decomposition(
        crs=work_area.crs,
        cells=cells,
        azimuth_deg=azimuth % 360.0,
        cell_size_m=size_m,
        work_area_m2=float(mask.area),
        warnings=warnings,
        stub=True,
    )


__all__ = ["cell_size_m", "decompose", "farthest_reach_m", "sortie_capacity_m2"]
