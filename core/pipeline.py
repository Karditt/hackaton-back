"""Сборка конвейера: Scenario → SurveyPlan → CoverageSet → Allocation → Mission.

Каждая стадия — чистое преобразование типизированного входа в типизированный
выход. Конвейер знает порядок стадий и больше ничего: ни геометрии, ни физики,
ни оптимизации здесь нет и быть не должно.

Стадия, работающая на заглушке, обязана сообщить о себе через
`Mission.stub_stages` и предупреждения — план на заглушках не должен
выглядеть как настоящий расчёт (AGENT.md §5).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from core import assumptions
from core.allocate.baseline import build_baseline_allocation
from core.allocate.objective import objective_value
from core.catalog import VehicleCatalog, load_catalog
from core.coverage.strips import build_coverage
from core.errors import InfeasibleError
from core.flight.cost import (
    build_sortie,
    endurance_limit_s,
    estimate_sortie_duration_s,
)
from core.geo.mask import WorkArea, build_work_area
from core.schemas import (
    Allocation,
    CoverageSet,
    Mission,
    MissionMetrics,
    PipelineResult,
    PlanWarning,
    Scenario,
    Severity,
    Sortie,
    Stage,
    Strip,
    SurveyPlan,
    Vehicle,
)
from core.survey.plan import build_survey_plan
from core.transit.routing import make_router
from core.validate.checks import validate_mission

#: Стадии, работающие на заглушках. Список сокращается по мере закрытия эпиков
#: и напрямую попадает в `Mission.stub_stages`, KML и GeoJSON.
STUB_STAGES: tuple[Stage, ...] = (
    Stage.SURVEY,
    Stage.COVERAGE,
    Stage.FLIGHT,
    Stage.TRANSIT,
    Stage.VALIDATE,
)


@dataclass
class PlanningRun:
    """Результат прогона вместе с несериализуемым контекстом.

    `PipelineResult` уходит в API и на фронт, `WorkArea` остаётся в процессе:
    в ней живёт `Projector`, который нужен экспорту, чтобы вернуть метры
    обратно в WGS84.
    """

    scenario: Scenario
    result: PipelineResult
    work_area: WorkArea
    catalog: VehicleCatalog
    vehicles: dict[str, Vehicle] = field(default_factory=dict)

    @property
    def mission(self) -> Mission:
        """Готовый план."""
        return self.result.mission


class _Clock:
    """Секундомер стадий конвейера."""

    def __init__(self) -> None:
        self.timings_s: dict[str, float] = {}
        self._started = time.perf_counter()

    def lap(self, stage: str) -> None:
        """Зафиксировать длительность очередной стадии."""
        now = time.perf_counter()
        self.timings_s[stage] = now - self._started
        self._started = now


def resolve_fleet(scenario: Scenario, catalog: VehicleCatalog) -> dict[str, Vehicle]:
    """Достать из каталога борта, упомянутые во флоте сценария.

    Args:
        scenario: сценарий.
        catalog: каталог техники.

    Returns:
        Борта по идентификаторам.

    Raises:
        CatalogError: во флоте указан борт, которого нет в каталоге.
    """
    return {unit.vehicle_id: catalog.get(unit.vehicle_id) for unit in scenario.fleet}


def _metrics(
    sorties: list[Sortie], coverage: CoverageSet, alpha: float, vehicles: int
) -> MissionMetrics:
    """Посчитать метрики плана.

    Args:
        sorties: собранные вылеты.
        coverage: построенное покрытие.
        alpha: вес makespan в целевой функции.
        vehicles: число задействованных физических бортов.

    Returns:
        Метрики плана.
    """
    makespan_s = max((sortie.end_time_s() for sortie in sorties), default=0.0)
    total_flight_time_s = sum(sortie.duration_s for sortie in sorties)
    total_distance_m = sum(sortie.distance_m for sortie in sorties)
    covered = {strip_id for sortie in sorties for strip_id in sortie.strip_ids}

    return MissionMetrics(
        makespan_s=makespan_s,
        total_flight_time_s=total_flight_time_s,
        total_distance_m=total_distance_m,
        sortie_count=len(sorties),
        vehicle_count=vehicles,
        strips_planned=len(coverage.strips),
        strips_covered=len(covered),
        objective_value=objective_value(makespan_s, total_flight_time_s, alpha),
        alpha=alpha,
    )


def run_pipeline(
    scenario: Scenario,
    catalog: VehicleCatalog | None = None,
    alpha: float | None = None,
    seed: int | None = None,
) -> PlanningRun:
    """Прогнать сценарий через весь конвейер.

    Args:
        scenario: сценарий с координатами в WGS84.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        alpha: переопределение веса makespan, 0..1. None — взять из сценария.
        seed: переопределение зерна ГПСЧ. None — взять из сценария.

    Returns:
        Прогон: все стадии конвейера плюс рабочая область для экспорта.

    Raises:
        GeometryError: область вырождена или рабочая маска пуста.
        InfeasibleError: флот непригоден для заданной съёмки.
        CatalogError: во флоте указан борт вне каталога.
    """
    if alpha is not None:
        scenario = scenario.model_copy(update={"alpha": alpha})
    if seed is not None:
        scenario = scenario.model_copy(update={"seed": seed})

    clock = _Clock()
    vehicle_catalog = catalog or load_catalog()
    vehicles = resolve_fleet(scenario, vehicle_catalog)

    work_area = build_work_area(scenario)
    clock.lap("geo")

    survey_plan = build_survey_plan(scenario, vehicles)
    clock.lap("survey")

    coverage = build_coverage(work_area, survey_plan.reference())
    clock.lap("coverage")

    usable_ids = [params.vehicle_id for params in survey_plan.per_vehicle]
    strips_by_id = {strip.id: strip for strip in coverage.strips}

    def fits(vehicle: Vehicle, site_id: str, strips: list[Strip]) -> bool:
        """Помещается ли набор галсов в один вылет борта с указанной площадки.

        Оценка обязана совпадать с итоговым расчётом `build_sortie`, иначе
        распределение соберёт вылет, который валидатор тут же забракует.
        Поэтому площадка берётся именно та, с которой борт полетит.
        """
        if not strips:
            return True
        params = survey_plan.for_vehicle(vehicle.id)
        duration_s = estimate_sortie_duration_s(
            vehicle, params, work_area.site(site_id), strips, scenario.wind
        )
        return duration_s <= endurance_limit_s(vehicle)

    allocation = build_baseline_allocation(scenario, coverage, vehicles, usable_ids, fits)
    clock.lap("allocate")

    sorties, flight_warnings = _schedule_with_strips(
        allocation, work_area, scenario, survey_plan, vehicles, strips_by_id
    )
    clock.lap("flight")

    instances = {(sortie.vehicle_id, sortie.vehicle_instance) for sortie in sorties}
    metrics = _metrics(sorties, coverage, scenario.alpha, len(instances))

    validation_warnings = validate_mission(scenario, coverage, sorties, vehicles)
    clock.lap("validate")

    mission = Mission(
        id=f"mission-{uuid.uuid4().hex[:8]}",
        scenario_id=scenario.id,
        crs=work_area.crs,
        origin=work_area.projector.to_geo(work_area.sites[scenario.sites[0].id]),
        sorties=sorties,
        metrics=metrics,
        warnings=[
            *work_area.warnings,
            *flight_warnings,
            *validation_warnings,
        ],
        algorithm=allocation.algorithm,
        alpha=scenario.alpha,
        seed=scenario.seed,
        stub_stages=list(STUB_STAGES),
    )

    if not sorties:
        mission.warnings.append(
            PlanWarning(
                code="pipeline.empty_plan",
                severity=Severity.ERROR,
                stage=Stage.ALLOCATE,
                message="План пуст: ни один вылет не собран. Сценарий невыполним имеющимся флотом",
            )
        )

    result = PipelineResult(
        scenario_id=scenario.id,
        crs=work_area.crs,
        survey_plan=survey_plan,
        coverage=coverage,
        allocation=allocation,
        mission=mission,
        timings_s=clock.timings_s,
    )
    return PlanningRun(
        scenario=scenario,
        result=result,
        work_area=work_area,
        catalog=vehicle_catalog,
        vehicles=vehicles,
    )


def _schedule_with_strips(
    allocation: Allocation,
    work_area: WorkArea,
    scenario: Scenario,
    survey_plan: SurveyPlan,
    vehicles: dict[str, Vehicle],
    strips_by_id: dict[str, Strip],
) -> tuple[list[Sortie], list[PlanWarning]]:
    """Собрать вылеты по назначениям и расставить их во времени.

    Args:
        allocation: распределение галсов по вылетам.
        work_area: рабочая область (площадки, запретные зоны, проекция).
        scenario: сценарий (ветер).
        survey_plan: параметры съёмки по бортам.
        vehicles: каталог бортов.
        strips_by_id: галсы покрытия по идентификаторам.

    Returns:
        Пара «вылеты, предупреждения».
    """
    router = make_router(work_area.no_fly)
    sorties: list[Sortie] = []
    warnings: list[PlanWarning] = []
    busy_until_s: dict[tuple[str, int], float] = {}

    for assignment in allocation.assignments:
        vehicle = vehicles[assignment.vehicle_id]
        key = (assignment.vehicle_id, assignment.vehicle_instance)
        start_s = (
            assumptions.VEHICLE_PREP_TIME_S
            if key not in busy_until_s
            else busy_until_s[key] + vehicle.battery_swap_time_s
        )

        try:
            sortie, sortie_warnings = build_sortie(
                sortie_id=assignment.sortie_id,
                vehicle=vehicle,
                params=survey_plan.for_vehicle(assignment.vehicle_id),
                site_id=assignment.site_id,
                site=work_area.site(assignment.site_id),
                strips=[strips_by_id[strip_id] for strip_id in assignment.strip_ids],
                wind=scenario.wind,
                transit=router,
                start_time_s=start_s,
                vehicle_instance=assignment.vehicle_instance,
                trip_index=assignment.trip_index,
            )
        except InfeasibleError as error:
            warnings.append(
                PlanWarning(
                    code="flight.infeasible",
                    severity=Severity.ERROR,
                    stage=Stage.FLIGHT,
                    message=f"Вылет {assignment.sortie_id} не собран: {error}",
                    context={
                        "sortie_id": assignment.sortie_id,
                        "vehicle_id": assignment.vehicle_id,
                    },
                )
            )
            continue

        busy_until_s[key] = sortie.end_time_s()
        sorties.append(sortie)
        warnings.extend(sortie_warnings)

    return sorties, warnings


__all__ = ["PlanningRun", "STUB_STAGES", "resolve_fleet", "run_pipeline"]
