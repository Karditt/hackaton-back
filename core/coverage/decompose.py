"""Декомпозиция маски съёмки на подобласти — единицы распределения (E3).

Ядро задачи — разделение работ между бортами (заказчик, №46). Область не
режется одной сеткой галсов на весь флот: она делится на подобласти, они
распределяются по вылетам, и только потом каждый борт строит в своей
подобласти галсы со своим шагом (№27). Компактная подобласть на борт
разводит борта в плане естественным образом (№37, №38).

Настоящая область заказчика — сотни островков медианой 5,4 га (долг №46),
поэтому единица нарезки — островок маски:

- островок не больше ячейки — одна подобласть целиком, сетка его не режет;
- крупный режется на равные части в своих осях (по длинной стороне
  описанного прямоугольника), а не общей сеткой области;
- обрезок меньше `CELL_MERGE_FRACTION` ячейки сливается с соседней частью
  того же островка — с той, у которой самая длинная общая граница.

Размер ячейки — по самому «малоёмкому» борту флота (`cell_size_m`).
Подобласти упорядочены змейкой по рядам крупной сетки в осях всей области:
baseline режет этот список на связные куски.
"""

from __future__ import annotations

import math

import numpy as np
from shapely import affinity
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.coverage.oracle import fit_strips, maneuver_hazard
from core.coverage.strips import candidate_azimuths, cover_cell, dominant_azimuth_deg
from core.flight.cost import endurance_limit_s, turn_length_m
from core.geo.mask import WorkArea
from core.geo.project import polygon_from_shapely, polygons_of
from core.schemas import (
    Cell,
    Decomposition,
    MetricPoint,
    PlanWarning,
    Severity,
    Stage,
    SurveyParams,
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

    tiny_m2 = assumptions.GEOMETRY_TOLERANCE_M**2
    islands = [part for part in polygons_of(mask) if part.area > tiny_m2]
    pieces: list[Polygon] = []
    split = 0
    for island in islands:
        if island.area <= size_m**2:
            pieces.append(island)
            continue
        split += 1
        pieces.extend(split_island(island, size_m))
    pieces, carved = _carve_for_fixed_wing(pieces, work_area, survey_plan, vehicles, size_m, sites)
    fleet: _Fleet = [(params, vehicles[params.vehicle_id]) for params in survey_plan.per_vehicle]
    min_side_m = max(params.strip_spacing_m for params in survey_plan.per_vehicle)
    refined = [part for piece in pieces for part in _refine(piece, fleet, sites, min_side_m)]
    halved = len(refined) - len(pieces)
    pieces = refined

    # Порядок — змейка по рядам крупной сетки в осях всей области: соседние в
    # списке подобласти соседствуют и на местности.
    math_angle_deg = 90.0 - azimuth
    pivot = mask.centroid
    turned = [
        affinity.rotate(piece.centroid, -math_angle_deg, origin=pivot, use_radians=False)
        for piece in pieces
    ]
    min_x = min(point.x for point in turned)
    min_y = min(point.y for point in turned)
    placed = []
    for piece, point in zip(pieces, turned, strict=True):
        row = int((point.y - min_y) // size_m)
        col = int((point.x - min_x) // size_m)
        serpentine = point.x if row % 2 == 0 else -point.x
        placed.append((row, col if row % 2 == 0 else -col, serpentine, row, col, piece))
    placed.sort(key=lambda item: item[:3])

    cells: list[Cell] = []
    seen: dict[tuple[int, int], int] = {}
    for _row_key, _col_key, _x, row, col, piece in placed:
        index = seen.get((row, col), 0)
        seen[(row, col)] = index + 1
        centroid = piece.centroid
        cells.append(
            Cell(
                id=f"c{row:03d}-{col:03d}-{index}",
                polygon=polygon_from_shapely(piece),
                area_m2=float(piece.area),
                centroid=MetricPoint(x_m=centroid.x, y_m=centroid.y),
                row=row,
                col=col,
            )
        )

    warnings = [
        PlanWarning(
            code="decompose.islands",
            severity=Severity.INFO,
            stage=Stage.DECOMPOSE,
            message=(
                f"Подобласти — островки маски: {len(islands)} островков, из них {split} "
                f"крупнее ячейки {size_m:.0f} × {size_m:.0f} м нарезаны на равные части в "
                f"своих осях, обрезки меньше {assumptions.CELL_MERGE_FRACTION:.0%} ячейки "
                f"слиты с соседями. У {carved} подобластей, которые не снял бы ни один "
                "самолёт, полоса у запретного отрезана отдельно — остальное самолёту "
                f"доступно. Дальние подобласти, непосильные ни одному борту за вылет, "
                f"поделены пополам (+{halved}). Всего подобластей: {len(cells)}"
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
        stub=False,
    )


def split_island(island: Polygon, size_m: float) -> list[Polygon]:
    """Нарезать крупный островок на части не больше ячейки.

    Островок поворачивается так, чтобы его длинная сторона легла по оси X, и
    делится на равные прямоугольники: `ceil(ширина / size_m)` столбцов и
    столько же по высоте. Равные доли вместо квадратов фиксированного размера
    не оставляют узкой полосы у дальнего края. Затем мелкие обрезки
    сливаются с соседями (`_merge_small`).

    Args:
        island: полигон островка, метры.
        size_m: сторона ячейки, метры.

    Returns:
        Части островка, метры; вместе — ровно островок.
    """
    # Поворачиваются прямоугольники, а не островок: повёрнутый полигон с узким
    # выступом из-за округления становится самопересекающимся (см. cover_cell).
    radians = math.radians(dominant_azimuth_deg(island))
    along = (math.sin(radians), math.cos(radians))
    across = (-math.cos(radians), math.sin(radians))
    ring = np.asarray(island.exterior.coords)
    x_along = ring @ np.array(along)
    y_across = ring @ np.array(across)
    min_x, max_x = float(x_along.min()), float(x_along.max())
    min_y, max_y = float(y_across.min()), float(y_across.max())
    columns = max(1, math.ceil((max_x - min_x) / size_m))
    rows = max(1, math.ceil((max_y - min_y) / size_m))
    step_x = (max_x - min_x) / columns
    step_y = (max_y - min_y) / rows

    def point(x: float, y: float) -> tuple[float, float]:
        return (x * along[0] + y * across[0], x * along[1] + y * across[1])

    fragments: list[Polygon] = []
    for row in range(rows):
        for col in range(columns):
            x0, x1 = min_x + col * step_x, min_x + (col + 1) * step_x
            y0, y1 = min_y + row * step_y, min_y + (row + 1) * step_y
            frame = Polygon([point(x0, y0), point(x1, y0), point(x1, y1), point(x0, y1)])
            fragments.extend(
                part
                for part in polygons_of(island.intersection(frame))
                if part.area > assumptions.GEOMETRY_TOLERANCE_M**2
            )
    return _merge_small(fragments, assumptions.CELL_MERGE_FRACTION * size_m**2)


_Fleet = list[tuple[SurveyParams, Vehicle]]


def _nearest_site_m(piece: Polygon, sites: list[MetricPoint]) -> float:
    """До дальней точки подобласти от ближайшей к ней площадки, метры.

    Заход и выход — на краях подобласти, а не в центре; дальний край даёт
    оценку подлёта с запасом.
    """
    ring = np.asarray(piece.exterior.coords)
    return min(
        float(np.hypot(ring[:, 0] - site.x_m, ring[:, 1] - site.y_m).max()) for site in sites
    )


def _overhead_s(params: SurveyParams, distance_m: float) -> float:
    """Взлёт, посадка, набор, снижение и подлёт туда-обратно, секунды.

    Эшелон перелёта неизвестен до назначения эшелонов, но он не ниже высоты
    съёмки плюс вертикальный интервал: подъём и спуск считаются до него.
    """
    transit_altitude_m = params.altitude_agl_m + assumptions.VERTICAL_SEPARATION_M
    return (
        assumptions.TAKEOFF_TIME_S
        + assumptions.LANDING_TIME_S
        + transit_altitude_m / assumptions.CLIMB_RATE_MPS
        + transit_altitude_m / assumptions.DESCENT_RATE_MPS
        + 2.0 * distance_m / params.transit_speed_mps
    )


def _single_sortie_s(
    piece: Polygon, params: SurveyParams, vehicle: Vehicle, distance_m: float
) -> float:
    """Прикидка вылета на одну подобласть с площадки в `distance_m` метрах, секунды.

    По настоящим галсам лучшего направления и той же длине разворота, что в
    модели полёта (`turn_length_m`): у узкой изрезанной подобласти развороты
    съедают больше, чем даёт оценка ёмкости по площади. Обходы препятствий на
    подлёте и эшелон перелёта не считаются — окончательно решает распределение.
    """
    azimuth = candidate_azimuths(piece, params.strip_spacing_m)[0]
    strips = cover_cell("probe", piece, params, azimuth)
    turns_m = sum(
        turn_length_m(vehicle, before.end(), after.start())
        for before, after in zip(strips, strips[1:], strict=False)
    )
    return (
        _overhead_s(params, distance_m)
        + turns_m / params.transit_speed_mps
        + sum(strip.length_m for strip in strips) / params.survey_speed_mps
    )


def _fits(piece: Polygon, fleet: _Fleet, sites: list[MetricPoint]) -> bool:
    """Снимет ли подобласть за один вылет хоть один борт из `fleet`."""
    if not fleet or not sites:
        return False
    distance_m = _nearest_site_m(piece, sites)
    return any(
        _single_sortie_s(piece, params, vehicle, distance_m) <= endurance_limit_s(vehicle)
        for params, vehicle in fleet
    )


def _refine(
    piece: Polygon, fleet: _Fleet, sites: list[MetricPoint], min_side_m: float
) -> list[Polygon]:
    """Делить подобласть пополам, пока её не снимет за вылет хоть один борт.

    Размер ячейки один на всю область, а подлёт к дальнему краю у бортов с
    малой автономностью съедает почти весь заряд: там нужна ячейка мельче.
    Деление останавливается, когда длинная сторона короче `min_side_m` (шага
    галсов: мельче в половину не ляжет галс) или когда туда не долетает никто
    даже без съёмки — мельче резать бесполезно, это невыполнимость, и
    распределение назовёт причину.
    """
    if not fleet or not sites or _fits(piece, fleet, sites):
        return [piece]
    distance_m = _nearest_site_m(piece, sites)
    if all(_overhead_s(p, distance_m) >= endurance_limit_s(v) for p, v in fleet):
        return [piece]
    halves = _halve(piece, min_side_m)
    if len(halves) < 2:
        return [piece]
    return [part for item in halves for part in _refine(item, fleet, sites, min_side_m)]


def _halve(piece: Polygon, min_side_m: float) -> list[Polygon]:
    """Разрезать подобласть поперёк длинной стороны посередине; короче `min_side_m` — не резать."""
    radians = math.radians(dominant_azimuth_deg(piece))
    along = (math.sin(radians), math.cos(radians))
    across = (-math.cos(radians), math.sin(radians))
    ring = np.asarray(piece.exterior.coords)
    x_along = ring @ np.array(along)
    y_across = ring @ np.array(across)
    low, high = float(x_along.min()), float(x_along.max())
    if high - low < min_side_m:
        return [piece]
    middle = (low + high) / 2.0
    bottom, top = float(y_across.min()), float(y_across.max())

    def point(x: float, y: float) -> tuple[float, float]:
        return (x * along[0] + y * across[0], x * along[1] + y * across[1])

    parts: list[Polygon] = []
    for start, end in ((low, middle), (middle, high)):
        frame = Polygon(
            [point(start, bottom), point(end, bottom), point(end, top), point(start, top)]
        )
        parts.extend(
            part
            for part in polygons_of(piece.intersection(frame))
            if part.area > assumptions.GEOMETRY_TOLERANCE_M**2
        )
    return parts


def _carve_for_fixed_wing(
    pieces: list[Polygon],
    work_area: WorkArea,
    survey_plan: SurveyPlan,
    vehicles: dict[str, Vehicle],
    size_m: float,
    sites: list[MetricPoint],
) -> tuple[list[Polygon], int]:
    """Отрезать полосу у запретного от подобластей, которые иначе никто не снимет.

    Самолёту нужна зона манёвра на концах галсов (№38). Подобласть, прижатая к
    запретной зоне так, что при любом направлении галсов разворот задевает
    зону, самолёту недоступна целиком — даже если у зоны лишь её край (долг
    №51). Пока до неё долетает мультиротор, это не беда: подобласть его. Если
    не долетает ни один, подобласть делится: ядро — дальше зоны манёвра от
    запретного и от границы пространства, его снимает самолёт при любом
    направлении; полоса — отдельными подобластями для самолёта с галсами вдоль
    границы или для мультиротора с другой площадки. Ядро считается по самолёту с
    наименьшим радиусом разворота: ему оно заведомо доступно.

    Args:
        pieces: подобласти, метры.
        work_area: рабочая область (зоны, препятствия, граница пространства).
        survey_plan: параметры съёмки пригодных бортов.
        vehicles: каталог бортов.
        size_m: сторона ячейки, метры — полоса длиннее ячейки режется.
        sites: штатные площадки, метры — от ближайшей считается подлёт мультиротора.

    Returns:
        Пара «подобласти после разреза, сколько подобластей разрезано».
    """
    planes = [
        (params, vehicles[params.vehicle_id])
        for params in survey_plan.per_vehicle
        if vehicles[params.vehicle_id].is_fixed_wing()
        and vehicles[params.vehicle_id].turn_radius_m > 0.0
    ]
    if not planes:
        return pieces, 0
    copters = [
        (params, vehicles[params.vehicle_id])
        for params in survey_plan.per_vehicle
        if not vehicles[params.vehicle_id].is_fixed_wing()
    ]
    min_side_m = 2.0 * max(params.strip_spacing_m for params in survey_plan.per_vehicle)

    hazards = {
        params.vehicle_id: maneuver_hazard(work_area, params.altitude_agl_m) for params, _ in planes
    }
    airspace = work_area.airspace
    nimble, nimble_vehicle = min(
        planes, key=lambda item: (item[1].turn_radius_m, item[0].vehicle_id)
    )
    reach_m = (
        assumptions.MANEUVER_ZONE_RADIUS_FACTOR * nimble_vehicle.turn_radius_m
        + assumptions.BOUNDARY_TOLERANCE_M
    )
    hazard = hazards[nimble.vehicle_id]
    keep_out = hazard.buffer(reach_m) if not hazard.is_empty else None
    inside = airspace.buffer(-reach_m) if airspace is not None else None
    tiny_m2 = assumptions.GEOMETRY_TOLERANCE_M**2

    result: list[Polygon] = []
    carved = 0
    for index, piece in enumerate(pieces):
        crowded = (keep_out is not None and piece.intersects(keep_out)) or (
            inside is not None and not inside.contains(piece)
        )
        if (
            not crowded
            or _fits(piece, copters, sites)
            or any(
                fit_strips(
                    f"probe-{index}", piece, params, vehicle, hazards[params.vehicle_id], airspace
                )[2]
                is None
                for params, vehicle in planes
            )
        ):
            result.append(piece)
            continue
        core: BaseGeometry = piece.difference(keep_out) if keep_out is not None else piece
        if inside is not None:
            core = core.intersection(inside)
        band = piece.difference(core)
        core_parts = [part for part in polygons_of(core) if part.area > tiny_m2]
        band_parts: list[Polygon] = []
        for part in polygons_of(band):
            if part.area <= tiny_m2:
                continue
            # Полосу снимает мультиротор: режем её на куски ему по силам.
            pieces_of_band = split_island(part, size_m) if part.area > size_m**2 else [part]
            for item in pieces_of_band:
                band_parts.extend(_refine(item, copters, sites, min_side_m))
        if not core_parts:
            result.append(piece)
            continue
        carved += 1
        result.extend(core_parts)
        result.extend(band_parts)
    return result, carved


def _merge_small(fragments: list[Polygon], threshold_m2: float) -> list[Polygon]:
    """Слить обрезки меньше `threshold_m2` с соседом по самой длинной общей границе.

    Обрезок, у которого нет соседа с общей границей, остаётся как есть.
    """
    parts = list(fragments)
    while True:
        small = [
            index for index, part in enumerate(parts) if part.area < threshold_m2 and len(parts) > 1
        ]
        best: tuple[float, int, int] | None = None
        for index in sorted(small, key=lambda item: parts[item].area):
            shared = [
                (parts[index].intersection(other).length, other_index)
                for other_index, other in enumerate(parts)
                if other_index != index and parts[index].touches(other)
            ]
            shared = [item for item in shared if item[0] > assumptions.GEOMETRY_TOLERANCE_M]
            if shared:
                length, neighbour = max(shared)
                best = (length, index, neighbour)
                break
        if best is None:
            return parts
        _length, index, neighbour = best
        union = unary_union([parts[index], parts[neighbour]])
        pieces = polygons_of(union)
        if len(pieces) != 1:
            return parts
        parts[neighbour] = pieces[0]
        del parts[index]


__all__ = ["cell_size_m", "decompose", "farthest_reach_m", "sortie_capacity_m2", "split_island"]
