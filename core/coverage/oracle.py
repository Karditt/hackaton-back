"""Покрытие пар «подобласть × борт» по запросу, с кешем.

Распределению (E6) нужно знать, во что обойдётся борту подобласть и может
ли он её снять вообще. Покрытие строится лениво — только для пар, которые
распределение действительно рассматривает, — и кешируется.

Направление галсов выбирается на пару «подобласть × борт»: кандидаты из
`candidate_azimuths` пробуются в порядке времени съёмки — с ветром скорость на
галсах своя у каждого направления (№45, долг №52), — берётся первый, при
котором пара пригодна. У мультиротора это всегда первый кандидат.

Пара непригодна, если при всех направлениях зона манёвра самолёта задевает
постоянную запретную зону на высоте съёмки, мешающее препятствие или выходит
за разрешённое пространство. Такую подобласть возьмёт мультиротор; если
некому — это невыполнимость с причиной, а не план с нарушением (инвариант 9).
"""

from __future__ import annotations

from functools import partial

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.coverage.strips import (
    AxisSpeed,
    candidate_azimuths,
    cover_cell,
    covered_area_m2,
    maneuver_zones,
)
from core.flight.cost import interlace_step, loop_length_m
from core.geo.mask import WorkArea
from core.geo.project import polygon_to_shapely
from core.schemas import (
    Allocation,
    CellCoverage,
    CoverageSet,
    Decomposition,
    MetricPolygon,
    PlanWarning,
    Severity,
    Stage,
    Strip,
    SurveyParams,
    SurveyPlan,
    Vehicle,
    Wind,
)
from core.survey.plan import axis_survey_speed_mps


def maneuver_hazard(work_area: WorkArea, altitude_m: float) -> BaseGeometry:
    """Что не должна задевать зона манёвра на высоте съёмки, метры.

    Постоянные запретные зоны, запрещающие эту высоту, и препятствия выше неё с
    запасом — с буфером за вычетом допуска касания. Временные зоны — забота
    расписания (E14).

    Args:
        work_area: рабочая область.
        altitude_m: высота съёмки, м AGL.
    """
    zones = work_area.zones_blocking(altitude_m, altitude_m, permanent_only=True, inner=True)
    obstacles = work_area.obstacles_blocking(altitude_m, inner=True)
    return unary_union([zones, obstacles])


def _problem(
    strips: list[Strip],
    zones: list[MetricPolygon],
    hazard: BaseGeometry,
    airspace: BaseGeometry | None,
) -> str | None:
    """Почему борт не может снять подобласть этими галсами, либо None."""
    if not strips:
        return "подобласть уже шага галсов — ни одного галса не легло"
    if not zones:
        return None
    footprint = unary_union([polygon_to_shapely(zone) for zone in zones])
    if not hazard.is_empty and footprint.intersects(hazard):
        return "зона манёвра на концах галсов задевает запретную зону или высотное препятствие"
    if airspace is not None and not airspace.contains(footprint):
        return "зона манёвра на концах галсов выходит за разрешённое воздушное пространство"
    return None


def strip_interlace(params: SurveyParams, vehicle: Vehicle) -> int:
    """Через сколько линий борт облетает галсы (`interlace_step`); мультиротор — 1."""
    if not vehicle.is_fixed_wing():
        return 1
    return interlace_step(vehicle.turn_radius_m, params.strip_spacing_m)


def turn_time_s(params: SurveyParams, vehicle: Vehicle) -> float:
    """Время перехода на следующий галс, секунды — для выбора направления.

    Самолёт — разворот на галс через `strip_interlace` линий (`loop_length_m`,
    долг №61), мультиротор — по прямой на шаг; оба — на путевой скорости
    перелёта.
    """
    if vehicle.is_fixed_wing() and vehicle.turn_radius_m > 0.0:
        lateral_m = strip_interlace(params, vehicle) * params.strip_spacing_m
        length_m = loop_length_m(vehicle.turn_radius_m, lateral_m)
    else:
        length_m = params.strip_spacing_m
    return length_m / params.transit_speed_mps


def fit_strips(
    cell_id: str,
    cell: Polygon,
    params: SurveyParams,
    vehicle: Vehicle,
    hazard: BaseGeometry,
    airspace: BaseGeometry | None,
    wind: Wind | None = None,
) -> tuple[list[Strip], list[MetricPolygon], str | None]:
    """Галсы подобласти в первом пригодном направлении.

    Кандидаты `candidate_azimuths` пробуются в порядке времени съёмки (без
    ветра — числа галсов); берётся первый, при котором зона манёвра ничего не
    задевает. Не подошёл ни один — возвращается лучший вместе с причиной.

    Args:
        cell_id: идентификатор подобласти.
        cell: полигон подобласти, метры.
        params: параметры съёмки борта.
        vehicle: борт.
        hazard: что не должна задевать зона манёвра (`maneuver_hazard`), метры.
        airspace: граница разрешённого пространства, метры; None — не задана.
        wind: ветер сценария. Задан и не штиль — скорость на галсах своя у
            каждого направления (`axis_survey_speed_mps`, долг №52), и
            направление выбирается по времени; None — скорость на любой курс.

    Returns:
        Галсы, зоны манёвра и причина непригодности (None — пригодна).
    """
    axis_speed: AxisSpeed | None = None
    if wind is not None and wind.speed_mps > 0.0:
        axis_speed = partial(axis_survey_speed_mps, vehicle, params, wind)

    first: tuple[list[Strip], list[MetricPolygon], str | None] | None = None
    for azimuth in candidate_azimuths(
        cell, params.strip_spacing_m, axis_speed, turn_time_s(params, vehicle)
    ):
        speed_mps = axis_speed(azimuth) if axis_speed is not None else None
        strips = cover_cell(
            cell_id, cell, params, azimuth, speed_mps, strip_interlace(params, vehicle)
        )
        zones = maneuver_zones(strips, vehicle)
        problem = _problem(strips, zones, hazard, airspace)
        if problem is None:
            return strips, zones, None
        if first is None:
            first = (strips, zones, problem)
    assert first is not None
    return first


class CoverageOracle:
    """Кеш покрытий «подобласть × борт»."""

    def __init__(
        self,
        decomposition: Decomposition,
        survey_plan: SurveyPlan,
        vehicles: dict[str, Vehicle],
        work_area: WorkArea,
        wind: Wind | None = None,
    ) -> None:
        """Args:
        decomposition: подобласти.
        survey_plan: параметры съёмки бортов.
        vehicles: каталог бортов.
        work_area: рабочая область.
        wind: ветер сценария — направление галсов выбирается с ним (`fit_strips`);
            None — скорость на галсах на любой курс.
        """
        self._decomposition = decomposition
        self._survey_plan = survey_plan
        self._vehicles = vehicles
        self._work_area = work_area
        self._wind = wind
        self._cells = {cell.id: polygon_to_shapely(cell.polygon) for cell in decomposition.cells}
        self._cache: dict[tuple[str, str], CellCoverage] = {}
        self._hazards: dict[float, BaseGeometry] = {}

    def cell_geometry(self, cell_id: str) -> Polygon:
        """Полигон подобласти, метры."""
        return self._cells[cell_id]

    def hazard_at(self, altitude_m: float) -> BaseGeometry:
        """`maneuver_hazard` на высоте съёмки, с кешем по высоте."""
        if altitude_m not in self._hazards:
            self._hazards[altitude_m] = maneuver_hazard(self._work_area, altitude_m)
        return self._hazards[altitude_m]

    def get(self, cell_id: str, vehicle_id: str) -> CellCoverage:
        """Покрытие подобласти бортом (строится при первом запросе).

        Args:
            cell_id: идентификатор подобласти.
            vehicle_id: идентификатор борта.

        Returns:
            Галсы, зоны манёвра, покрытая площадь, пригодность пары.
        """
        key = (cell_id, vehicle_id)
        if key in self._cache:
            return self._cache[key]

        params = self._survey_plan.for_vehicle(vehicle_id)
        vehicle = self._vehicles[vehicle_id]
        cell = self._cells[cell_id]
        if params is None:
            result = CellCoverage(
                cell_id=cell_id,
                vehicle_id=vehicle_id,
                covered_area_m2=0.0,
                serviceable=False,
                problem="борт исключён моделью съёмки",
            )
            self._cache[key] = result
            return result

        strips, zones, problem = fit_strips(
            cell_id,
            cell,
            params,
            vehicle,
            self.hazard_at(params.altitude_agl_m),
            self._work_area.airspace,
            self._wind,
        )
        result = CellCoverage(
            cell_id=cell_id,
            vehicle_id=vehicle_id,
            strips=strips,
            maneuver_zones=zones,
            covered_area_m2=covered_area_m2(strips, params.swath_width_m, cell),
            serviceable=problem is None,
            problem=problem,
        )
        self._cache[key] = result
        return result

    def serviceable(self, cell_id: str, vehicle_id: str) -> bool:
        """Может ли борт снять подобласть без нарушения жёстких правил."""
        return self.get(cell_id, vehicle_id).serviceable

    def coverage_set(self, allocation: Allocation) -> CoverageSet:
        """Покрытие подобластей, распределённых по вылетам.

        Args:
            allocation: распределение.

        Returns:
            Галсы и зоны манёвра всех назначенных пар «подобласть × борт».
        """
        cells: list[CellCoverage] = []
        for assignment in allocation.assignments:
            for cell_id in assignment.cell_ids:
                cells.append(self.get(cell_id, assignment.vehicle_id))
        warnings = [
            PlanWarning(
                code="coverage.azimuth_by_cell",
                severity=Severity.INFO,
                stage=Stage.COVERAGE,
                message=(
                    "Направление галсов выбрано на каждую подобласть и борт — наименьшее время "
                    "съёмки, при котором зона манёвра не задевает запретного. Время — галсы на "
                    "путевой скорости, которую борт удержит в обе стороны этого направления при "
                    "ветре сценария (поперёк ветра — выше), плюс развороты"
                ),
            )
        ]
        return CoverageSet(
            crs=self._decomposition.crs,
            cells=cells,
            work_area_m2=self._decomposition.work_area_m2,
            covered_area_m2=sum(cell.covered_area_m2 for cell in cells),
            warnings=warnings,
            stub=False,
        )


__all__ = ["CoverageOracle", "fit_strips", "maneuver_hazard"]
