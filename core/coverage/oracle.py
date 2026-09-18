"""Покрытие пар «подобласть × борт» по запросу, с кешем.

Распределению (E6) нужно знать, во что обойдётся борту подобласть и может
ли он её снять вообще. Покрытие строится лениво — только для пар, которые
распределение действительно рассматривает, — и кешируется.

Пара непригодна, если зона манёвра самолёта задевает постоянную запретную
зону на высоте съёмки, мешающее препятствие или выходит за разрешённое
пространство. Такую подобласть возьмёт мультиротор; если некому — это
невыполнимость с причиной, а не план с нарушением (инвариант 9).
"""

from __future__ import annotations

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.coverage.strips import cover_cell, covered_area_m2, maneuver_zones
from core.geo.mask import WorkArea
from core.geo.project import polygon_to_shapely
from core.schemas import (
    Allocation,
    CellCoverage,
    CoverageSet,
    Decomposition,
    PlanWarning,
    Severity,
    Stage,
    SurveyPlan,
    Vehicle,
)


class CoverageOracle:
    """Кеш покрытий «подобласть × борт»."""

    def __init__(
        self,
        decomposition: Decomposition,
        survey_plan: SurveyPlan,
        vehicles: dict[str, Vehicle],
        work_area: WorkArea,
    ) -> None:
        self._decomposition = decomposition
        self._survey_plan = survey_plan
        self._vehicles = vehicles
        self._work_area = work_area
        self._cells = {cell.id: polygon_to_shapely(cell.polygon) for cell in decomposition.cells}
        self._cache: dict[tuple[str, str], CellCoverage] = {}
        self._hazards: dict[str, BaseGeometry] = {}

    def cell_geometry(self, cell_id: str) -> Polygon:
        """Полигон подобласти, метры."""
        return self._cells[cell_id]

    def _hazard_for(self, vehicle_id: str, altitude_m: float) -> BaseGeometry:
        """Постоянные зоны и препятствия, мешающие борту на высоте съёмки."""
        if vehicle_id not in self._hazards:
            zones = self._work_area.zones_blocking(
                altitude_m, altitude_m, permanent_only=True, inner=True
            )
            obstacles = self._work_area.obstacles_blocking(altitude_m, inner=True)
            self._hazards[vehicle_id] = unary_union([zones, obstacles])
        return self._hazards[vehicle_id]

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

        strips = cover_cell(cell_id, cell, params, self._decomposition.azimuth_deg)
        zones = maneuver_zones(strips, vehicle)
        problem = self._problem(strips, zones, vehicle_id, params.altitude_agl_m)
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

    def _problem(self, strips, zones, vehicle_id: str, altitude_m: float) -> str | None:
        """Почему борт не может снять подобласть, либо None."""
        if not strips:
            return "подобласть уже шага галсов — ни одного галса не легло"
        if not zones:
            return None
        footprint = unary_union([polygon_to_shapely(zone) for zone in zones])
        hazard = self._hazard_for(vehicle_id, altitude_m)
        if not hazard.is_empty and footprint.intersects(hazard):
            return "зона манёвра на концах галсов задевает запретную зону или высотное препятствие"
        airspace = self._work_area.airspace
        if airspace is not None and not airspace.contains(footprint):
            return "зона манёвра на концах галсов выходит за разрешённое воздушное пространство"
        return None

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
                code="stub.coverage",
                severity=Severity.WARNING,
                stage=Stage.COVERAGE,
                message=(
                    f"Направление галсов {self._decomposition.azimuth_deg:.0f}° одно на всю "
                    "область и взято по её длинной стороне, а не выбрано с учётом ветра и "
                    "числа разворотов (TODO(E3))"
                ),
            )
        ]
        return CoverageSet(
            crs=self._decomposition.crs,
            cells=cells,
            work_area_m2=self._decomposition.work_area_m2,
            covered_area_m2=sum(cell.covered_area_m2 for cell in cells),
            warnings=warnings,
            stub=True,
        )


__all__ = ["CoverageOracle"]
