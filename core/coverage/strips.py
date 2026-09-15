"""Генерация галсов: параллельные полосы, обрезанные по рабочей маске.

ЗАГЛУШКА E3. Геометрия честная (метры, обрезка по маске, змейка), выбор
направления — нет: берётся длинная сторона описанного прямоугольника.
Настоящий E3 перебирает угол с целевой функцией, учитывающей ветер и число
разворотов, и режет область на подобласти.
"""

from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core import assumptions
from core.geo.mask import WorkArea
from core.schemas import (
    CoverageSet,
    MetricLineString,
    MetricPoint,
    PlanWarning,
    Severity,
    Stage,
    Strip,
    SurveyParams,
)

#: Запас, на который линия-резак выпускается за габариты области, метры.
#: Ровно по габаритам резать нельзя: после поворота диагональ длиннее стороны.
_CUT_MARGIN_M = 10.0


def _azimuth_from_vector(dx: float, dy: float) -> float:
    """Азимут вектора.

    Args:
        dx: приращение по X рабочей проекции, метры (на восток).
        dy: приращение по Y рабочей проекции, метры (на север).

    Returns:
        Азимут, градусы от севера по часовой стрелке, 0..360.
    """
    return math.degrees(math.atan2(dx, dy)) % 360.0


def dominant_azimuth_deg(geometry: BaseGeometry) -> float:
    """Направление длинной стороны минимального описанного прямоугольника.

    Галсы вдоль длинной стороны дают наименьшее число разворотов при прочих
    равных — но «прочие равные» здесь и заканчиваются: ветер не учитывается.

    TODO(E3): заменить перебором по углу с целевой функцией
    «время галсов + время разворотов + снос ветром».

    Args:
        geometry: рабочая маска в метрах.

    Returns:
        Азимут галсов, градусы 0..360.
    """
    rectangle = geometry.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)[:4]
    best_azimuth = 0.0
    best_length = -1.0
    for start, end in zip(coords, coords[1:] + coords[:1], strict=True):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length > best_length:
            best_length = length
            best_azimuth = _azimuth_from_vector(dx, dy)
    return best_azimuth % 180.0


def _lines_from(geometry: BaseGeometry) -> list[LineString]:
    """Разложить результат пересечения в список ломаных."""
    if geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    return [geom for geom in getattr(geometry, "geoms", []) if isinstance(geom, LineString)]


def build_coverage(
    work_area: WorkArea,
    params: SurveyParams,
    azimuth_deg: float | None = None,
) -> CoverageSet:
    """Покрыть рабочую маску галсами.

    Args:
        work_area: рабочая область в метрах (маска, проекция, площадки).
        params: параметры съёмки опорного борта: шаг галсов, высота, скорость.
        azimuth_deg: принудительное направление галсов, градусы. None —
            выбрать по длинной стороне области.

    Returns:
        Набор галсов в метрах плюс предупреждения стадии.
    """
    mask = work_area.mask
    azimuth = dominant_azimuth_deg(mask) if azimuth_deg is None else azimuth_deg % 360.0

    # Поворачиваем маску так, чтобы галсы легли горизонтально: резать
    # горизонтальными линиями проще и устойчивее, чем наклонными.
    math_angle_deg = 90.0 - azimuth
    pivot = mask.centroid
    rotated = affinity.rotate(mask, -math_angle_deg, origin=pivot, use_radians=False)

    min_x, min_y, max_x, max_y = rotated.bounds
    spacing_m = max(params.strip_spacing_m, assumptions.GEOMETRY_TOLERANCE_M)

    # Первая линия — на половину шага внутрь от края: так крайняя полоса
    # захвата накрывает границу области, а не свисает наружу наполовину.
    offsets: list[float] = []
    y = min_y + spacing_m / 2.0
    while y < max_y:
        offsets.append(y)
        y += spacing_m
    if not offsets:
        offsets.append((min_y + max_y) / 2.0)

    strips: list[Strip] = []
    warnings: list[PlanWarning] = []
    forward = True

    for index, offset_y in enumerate(offsets):
        cutter = LineString([(min_x - _CUT_MARGIN_M, offset_y), (max_x + _CUT_MARGIN_M, offset_y)])
        pieces = _lines_from(rotated.intersection(cutter))
        pieces.sort(key=lambda line: line.coords[0][0])

        for piece_index, piece in enumerate(pieces):
            if piece.length <= assumptions.GEOMETRY_TOLERANCE_M:
                continue
            restored = affinity.rotate(piece, math_angle_deg, origin=pivot, use_radians=False)
            coords = list(restored.coords)
            if not forward:
                coords.reverse()
            line = MetricLineString(points=[MetricPoint(x_m=x, y_m=y) for x, y in coords])
            heading = _azimuth_from_vector(
                coords[-1][0] - coords[0][0], coords[-1][1] - coords[0][1]
            )
            strips.append(
                Strip(
                    id=f"s{index:04d}-{piece_index}",
                    line=line,
                    length_m=restored.length,
                    altitude_agl_m=params.altitude_agl_m,
                    speed_mps=params.survey_speed_mps,
                    azimuth_deg=heading,
                )
            )
        forward = not forward

    if not strips:
        warnings.append(
            PlanWarning(
                code="coverage.empty",
                severity=Severity.ERROR,
                stage=Stage.COVERAGE,
                message=(
                    f"Рабочая маска не покрылась галсами при шаге {spacing_m:.0f} м — "
                    "область меньше одного галса"
                ),
            )
        )

    covered = _covered_area_m2(strips, params.swath_width_m, mask)
    coverage_fraction = covered / mask.area if mask.area > 0 else 0.0
    if strips and coverage_fraction < 0.99:
        warnings.append(
            PlanWarning(
                code="coverage.incomplete",
                severity=Severity.WARNING,
                stage=Stage.COVERAGE,
                message=(
                    f"Полосы захвата покрывают {coverage_fraction * 100:.1f}% рабочей маски. "
                    "TODO(E3): нет декомпозиции области и перекрытия на стыках подобластей"
                ),
            )
        )

    warnings.append(
        PlanWarning(
            code="stub.coverage",
            severity=Severity.WARNING,
            stage=Stage.COVERAGE,
            message=(
                f"Направление галсов {azimuth:.0f}° взято по длинной стороне области, "
                "а не рассчитано перебором с учётом ветра и числа разворотов (TODO(E3))"
            ),
        )
    )

    return CoverageSet(
        crs=work_area.crs,
        strips=strips,
        azimuth_deg=azimuth % 360.0,
        work_area_m2=mask.area,
        covered_area_m2=covered,
        warnings=warnings,
        stub=True,
    )


def _covered_area_m2(strips: list[Strip], swath_width_m: float, mask: BaseGeometry) -> float:
    """Площадь рабочей маски, реально накрытая полосами захвата, м²."""
    if not strips:
        return 0.0
    bands = [
        LineString([(point.x_m, point.y_m) for point in strip.line.points]).buffer(
            swath_width_m / 2.0, cap_style=2
        )
        for strip in strips
    ]
    union = unary_union(bands)
    if not isinstance(union, (Polygon, MultiPolygon)):
        return 0.0
    return float(union.intersection(mask).area)


__all__ = ["build_coverage", "dominant_azimuth_deg"]
