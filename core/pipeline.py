"""Сборка конвейера по ответам заказчика (PROJECT_BRIEF.md §2).

    Scenario → SurveyPlan → Decomposition → Allocation → Coverage
             → вылеты → Schedule → Validation → Mission → Files

Область сначала делится на подобласти, подобласти распределяются по вылетам,
и только потом каждый борт строит галсы в своей подобласти (заказчик, №27,
№46). Итог — полный план или отчёт о невыполнимости: частичных планов нет
(инвариант 10), план с нарушением жёсткого правила не выдаётся (инвариант 9).

Конвейер знает порядок стадий и больше ничего: ни геометрии, ни физики, ни
оптимизации здесь нет и быть не должно. Стадия, работающая на заглушке,
объявляет себя в `Mission.stub_stages` и предупреждениях (AGENT.md §5).
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from shapely.ops import unary_union

from core import assumptions
from core.advise import diagnose, stub_warning
from core.allocate.baseline import build_baseline_allocation
from core.allocate.fleet import FleetInstance, expand_fleet
from core.allocate.greedy import (
    STRATEGIES,
    GreedyStrategy,
    SortieBounds,
    build_greedy_allocation,
)
from core.allocate.objective import (
    criterion_key,
    objective_value,
    resource_s,
    service_share,
    weighted_value,
)
from core.allocate.search import improve_allocation
from core.catalog import VehicleCatalog, load_catalog, user_vehicle_warnings
from core.coverage.decompose import decompose
from core.coverage.oracle import CoverageOracle
from core.deconflict import ScheduleBook, assign_transit_levels, build_schedule
from core.deconflict.hops import SlopeClearance, SurveyHopPolicy
from core.deconflict.placer import BookPlacer
from core.errors import GeometryError
from core.flight.cost import CellEnds, build_sortie, cell_ends, endurance_limit_s, exceeds_range
from core.geo.mask import WorkArea, build_survey_mask, build_work_area
from core.schemas import (
    Allocation,
    CoverageSet,
    Decomposition,
    Mission,
    MissionMetrics,
    MissionStatus,
    OptimizationMode,
    PipelineResult,
    PlanWarning,
    Scenario,
    Schedule,
    Severity,
    Sortie,
    Stage,
    SurveyPlan,
    Vehicle,
)
from core.survey.plan import build_survey_plan
from core.survey.presets import resolve_overlaps
from core.transit.routing import make_router
from core.validate import validate_plan

#: Стадии, работающие на заглушках. Список сокращается по мере закрытия эпиков
#: и напрямую попадает в `Mission.stub_stages`, KML и GeoJSON. С 2026-09-29
#: пуст: расписание — задержка на земле с 4D-разведением, которую видит и
#: распределение (E14); рекомендации невыполнимого плана объявляют себя сами.
STUB_STAGES: tuple[Stage, ...] = ()

#: Алгоритмы распределения. По умолчанию — конструктивная эвристика с
#: локальным поиском (E6, шаги 2 и 3); эвристика без поиска и baseline всегда
#: доступны для сравнения (инвариант 8, E12).
ALLOCATORS = ("local_search", "constructive", "baseline")


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
        """Итог конвейера: план или отчёт о невыполнимости."""
        return self.result.mission


class _Clock:
    """Секундомер стадий конвейера."""

    def __init__(self) -> None:
        self.timings_s: dict[str, float] = {}
        self._started = time.perf_counter()

    def lap(self, stage: str) -> None:
        """Зафиксировать длительность очередной стадии."""
        now = time.perf_counter()
        self.timings_s[stage] = self.timings_s.get(stage, 0.0) + now - self._started
        self._started = now


def resolve_fleet(scenario: Scenario, catalog: VehicleCatalog) -> dict[str, Vehicle]:
    """Достать из каталога борта, упомянутые во флоте сценария.

    Raises:
        CatalogError: во флоте указан борт, которого нет в каталоге.
    """
    return {unit.vehicle_id: catalog.get(unit.vehicle_id) for unit in scenario.fleet}


def scenario_catalog(scenario: Scenario, catalog: VehicleCatalog | None = None) -> VehicleCatalog:
    """Каталог, по которому считается сценарий: файл плюс ТТХ пользователя.

    Args:
        scenario: сценарий; его `vehicles` накладываются поверх каталога.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.

    Raises:
        CatalogError: каталог не читается или модель пользователя противоречива.
    """
    return (catalog or load_catalog()).with_user_vehicles(scenario.vehicles)


def check_scenario(scenario: Scenario, catalog: VehicleCatalog | None = None) -> None:
    """Проверить сценарий на приёме, до расчёта (заказчик, №19).

    Ловит то, что является ошибкой входа, а не невыполнимостью задания:
    неизвестный борт, противоречивые ТТХ пользователя, некорректную геометрию
    (самопересечение — с местом), слишком большую область, перекрытие ниже
    минимума для типа съёмки.

    Raises:
        CatalogError, GeometryError, ScenarioError: с понятным текстом.
    """
    resolve_fleet(scenario, scenario_catalog(scenario, catalog))
    build_work_area(scenario)
    resolve_overlaps(scenario.survey_type, scenario.quality)


def _metrics(
    scenario: Scenario, sorties: list[Sortie], decomposition: Decomposition
) -> MissionMetrics:
    """Метрики выполнимого плана."""
    makespan_s = max((sortie.end_time_s() for sortie in sorties), default=0.0)
    total_flight_time_s = sum(sortie.duration_s for sortie in sorties)
    cycles = len(sorties)
    resource = resource_s(total_flight_time_s, cycles)
    covered = {cell_id for sortie in sorties for cell_id in sortie.cell_ids}
    return MissionMetrics(
        makespan_s=makespan_s,
        total_flight_time_s=total_flight_time_s,
        cycle_count=cycles,
        resource_s=resource,
        total_distance_m=sum(sortie.distance_m for sortie in sorties),
        sortie_count=len(sorties),
        vehicle_count=len({(sortie.vehicle_id, sortie.vehicle_instance) for sortie in sorties}),
        cells_planned=len(decomposition.cells),
        cells_covered=len(covered),
        objective_value=objective_value(scenario.mode, makespan_s, resource),
        weighted_value=weighted_value(scenario.alpha, makespan_s, resource),
        mode=scenario.mode,
        alpha=scenario.alpha,
        deadline_s=scenario.deadline_s,
        deadline_met=None if scenario.deadline_s is None else makespan_s <= scenario.deadline_s,
    )


def _service_note(sorties: list[Sortie], vehicles: dict[str, Vehicle]) -> PlanWarning | None:
    """Износ по нормативам ТО заказчика (№50, долг №44): доля межсервисного интервала.

    По каждой модели — самый нагруженный её борт. Модели без норматива
    перечисляются отдельно: число не выдумывается.
    """
    flights: dict[tuple[str, int], int] = {}
    flight_s: dict[tuple[str, int], float] = {}
    for sortie in sorties:
        key = (sortie.vehicle_id, sortie.vehicle_instance)
        flights[key] = flights.get(key, 0) + 1
        flight_s[key] = flight_s.get(key, 0.0) + sortie.duration_s
    worst: dict[str, float] = {}
    missing: set[str] = set()
    for key, count in flights.items():
        share = service_share(key[0], count, flight_s[key])
        if share is None:
            missing.add(key[0])
        else:
            worst[key[0]] = max(worst.get(key[0], 0.0), share)
    if not worst:
        return None
    parts = [
        f"{vehicles[model].name} — до {share * 100:.1f}% интервала на борт"
        for model, share in sorted(worst.items())
    ]
    tail = (
        f"; у {', '.join(sorted(vehicles[model].name for model in missing))} норматива нет"
        if missing
        else ""
    )
    return PlanWarning(
        code="plan.service_wear",
        severity=Severity.INFO,
        stage=Stage.ALLOCATE,
        message=(
            "Износ по нормативам ТО (201, 401 — 80 полётов, 801 — 160 ч): "
            + "; ".join(parts)
            + tail
        ),
        context={model: f"{share:.4f}" for model, share in worst.items()},
    )


def _errors(warnings: list[PlanWarning]) -> list[PlanWarning]:
    return [warning for warning in warnings if warning.severity is Severity.ERROR]


def _plan_key(
    scenario: Scenario, allocation: Allocation, sorties: list[Sortie]
) -> tuple[bool, bool, float, float]:
    """Ключ сравнения планов разных стратегий по критерию режима (`criterion_key`)."""
    return criterion_key(
        scenario.mode,
        scenario.deadline_s,
        max((sortie.end_time_s() for sortie in sorties), default=0.0),
        resource_s(sum(sortie.duration_s for sortie in sorties), len(sorties)),
        complete=not allocation.unassigned_cell_ids and bool(sorties),
    )


def _strategy_note(
    scenario: Scenario,
    tried: list[tuple[GreedyStrategy, Allocation, CoverageSet, list[Sortie], Schedule]],
    best: int,
) -> PlanWarning:
    """Какие стратегии распределения пробовались и какая взята."""
    parts = []
    for strategy, allocation, _coverage, sorties, _schedule in tried:
        if allocation.unassigned_cell_ids or not sorties:
            parts.append(f"«{strategy.name}» — невыполнимо")
            continue
        makespan_s = max(sortie.end_time_s() for sortie in sorties)
        flight_h = sum(sortie.duration_s for sortie in sorties) / 3600.0
        parts.append(
            f"«{strategy.name}» — срок {makespan_s / 3600.0:.2f} ч, налёт {flight_h:.1f} ч"
        )
    return PlanWarning(
        code="allocate.strategies",
        severity=Severity.INFO,
        stage=Stage.ALLOCATE,
        message=(
            "Распределение пробовало стратегии: "
            + "; ".join(parts)
            + f". Взята «{tried[best][0].name}» — лучшая по критерию режима "
            f"«{scenario.mode.value}»"
        ),
        context={"strategy": tried[best][0].name},
    )


#: Вылет-проба: борт, площадка (взлёт и посадка), подобласти по порядку.
_ProbeKey = tuple[str, int, str, tuple[str, ...]]

#: Вариант распределения вместе с его покрытием, вылетами и расписанием.
_Variant = tuple[Allocation, CoverageSet, list[Sortie], Schedule]

#: Сколько последних проб держать собранными: распределение оценивает
#: длительность вылета пробой и тут же ставит её в расписание, локальный
#: поиск переоценивает вылеты плана. На результат размер не влияет — только
#: на число повторных сборок.
_PROBE_CACHE_SIZE = 1024


@dataclass
class _Assembler:
    """Сборщик итогового `PlanningRun` — выполнимого или нет."""

    scenario: Scenario
    catalog: VehicleCatalog
    vehicles: dict[str, Vehicle]
    work_area: WorkArea
    survey_plan: SurveyPlan
    clock: _Clock
    decomposition: Decomposition | None = None
    allocation: Allocation | None = None
    coverage: CoverageSet | None = None
    warnings: list[PlanWarning] = field(default_factory=list)

    def finish(
        self,
        reasons: list[PlanWarning],
        sorties: list[Sortie] | None = None,
        schedule: Schedule | None = None,
        stub_stages: tuple[Stage, ...] = STUB_STAGES,
    ) -> PlanningRun:
        scenario = self.scenario
        feasible = not reasons and bool(sorties)
        stages = list(stub_stages)
        recommendations = []
        mission_warnings = list(self.warnings)
        if not feasible:
            recommendations = diagnose(
                scenario,
                self.work_area,
                self.survey_plan,
                self.decomposition,
                reasons,
                self.vehicles,
            )
            mission_warnings.append(stub_warning())
            stages.append(Stage.ADVISE)
        else:
            wear = _service_note(sorties or [], self.vehicles)
            if wear is not None:
                mission_warnings.append(wear)

        area_center = self.work_area.area.centroid
        mission = Mission(
            id=f"mission-{uuid.uuid4().hex[:8]}",
            scenario_id=scenario.id,
            crs=self.work_area.crs,
            origin=self.work_area.projector.xy_to_geo(area_center.x, area_center.y),
            status=MissionStatus.FEASIBLE if feasible else MissionStatus.INFEASIBLE,
            sorties=list(sorties or []) if feasible else [],
            schedule=schedule if feasible else None,
            metrics=(
                _metrics(scenario, sorties or [], self.decomposition)
                if feasible and self.decomposition is not None
                else None
            ),
            reasons=reasons,
            recommendations=recommendations,
            warnings=mission_warnings,
            algorithm=self.allocation.algorithm if self.allocation else "—",
            mode=scenario.mode,
            alpha=scenario.alpha,
            seed=scenario.seed,
            stub_stages=stages,
        )
        result = PipelineResult(
            scenario_id=scenario.id,
            crs=self.work_area.crs,
            survey_plan=self.survey_plan,
            decomposition=self.decomposition,
            allocation=self.allocation,
            coverage=self.coverage,
            mission=mission,
            timings_s=self.clock.timings_s,
        )
        return PlanningRun(
            scenario=scenario,
            result=result,
            work_area=self.work_area,
            catalog=self.catalog,
            vehicles=self.vehicles,
        )


def run_pipeline(
    scenario: Scenario,
    catalog: VehicleCatalog | None = None,
    mode: OptimizationMode | None = None,
    alpha: float | None = None,
    seed: int | None = None,
    allocator: str = ALLOCATORS[0],
    targets: bool = True,
) -> PlanningRun:
    """Прогнать сценарий через весь конвейер.

    Args:
        scenario: сценарий с координатами в WGS84.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        mode: переопределение режима оптимизации. None — из сценария.
        alpha: переопределение веса срока в свёртке, 0..1. None — из сценария.
        seed: переопределение зерна ГПСЧ. None — из сценария.
        allocator: алгоритм распределения из `ALLOCATORS`.
        targets: пробовать ли эвристику под целевые сроки
            (`ALLOCATE_TARGET_FACTORS`) — в режиме «минимум срока» это ещё
            столько же прогонов эвристики на стратегию. False — быстрее
            (подсказки по пулу считают десятки планов).

    Returns:
        Прогон: стадии конвейера и итог — план или отчёт о невыполнимости.

    Raises:
        GeometryError, ScenarioError, CatalogError: ошибка входа (некорректная
            геометрия, недопустимые значения, неизвестный борт). Невыполнимость
            задания — не исключение, а `Mission.status = infeasible`.
        ValueError: неизвестный алгоритм распределения.
    """
    if allocator not in ALLOCATORS:
        raise ValueError(f"Алгоритм распределения {allocator!r} неизвестен: {ALLOCATORS}")
    overrides = {
        key: value
        for key, value in (("mode", mode), ("alpha", alpha), ("seed", seed))
        if value is not None
    }
    if overrides:
        scenario = scenario.model_copy(update=overrides)

    clock = _Clock()
    vehicle_catalog = scenario_catalog(scenario, catalog)
    vehicles = resolve_fleet(scenario, vehicle_catalog)

    work_area = build_work_area(scenario)
    clock.lap("geo")

    survey_plan = build_survey_plan(scenario, vehicles)
    clock.lap("survey")

    assembler = _Assembler(
        scenario=scenario,
        catalog=vehicle_catalog,
        vehicles=vehicles,
        work_area=work_area,
        survey_plan=survey_plan,
        clock=clock,
        warnings=[
            *work_area.warnings,
            *user_vehicle_warnings(vehicles.values(), vehicle_catalog),
        ],
    )
    if not survey_plan.per_vehicle:
        return assembler.finish(_errors(survey_plan.warnings), stub_stages=())

    altitudes = [params.altitude_agl_m for params in survey_plan.per_vehicle]
    try:
        mask, mask_warnings = build_survey_mask(work_area, min(altitudes), max(altitudes))
    except GeometryError as error:
        reason = PlanWarning(
            code="geo.nothing_to_survey",
            severity=Severity.ERROR,
            stage=Stage.GEO,
            message=str(error),
        )
        return assembler.finish([reason], stub_stages=())
    assembler.warnings.extend(mask_warnings)

    decomposition = decompose(
        work_area, mask, survey_plan, vehicles, [site.id for site in scenario.primary_sites()]
    )
    assembler.decomposition = decomposition
    clock.lap("decompose")

    usable = [params.vehicle_id for params in survey_plan.per_vehicle]
    instances = expand_fleet(scenario, vehicles, usable)
    levels, level_warnings = assign_transit_levels(instances, survey_plan, work_area.corridor_max_m)
    oracle = CoverageOracle(decomposition, survey_plan, vehicles, work_area, scenario.wind)
    router = make_router(work_area)
    hops = SurveyHopPolicy(decomposition.cells, survey_plan, vehicles, work_area)
    slope = SlopeClearance(work_area)

    def make_sortie(
        instance: FleetInstance,
        cell_ids: list[str],
        takeoff_site_id: str,
        landing_site_id: str,
        sortie_id: str,
        trip_index: int,
    ) -> Sortie:
        params = survey_plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        own_area = unary_union([oracle.cell_geometry(cell_id) for cell_id in cell_ids])
        return build_sortie(
            sortie_id=sortie_id,
            vehicle=instance.vehicle,
            params=params,
            takeoff_site_id=takeoff_site_id,
            takeoff_site=work_area.site(takeoff_site_id),
            landing_site_id=landing_site_id,
            landing_site=work_area.site(landing_site_id),
            cells=[oracle.get(cell_id, instance.vehicle.id) for cell_id in cell_ids],
            transit_altitude_m=levels[instance.key],
            router=router,
            own_area=own_area,
            vehicle_instance=instance.index,
            trip_index=trip_index,
            survey_hop=hops.check(instance.vehicle.id, set(cell_ids), own_area),
            slope_check=slope,
        )

    probes: OrderedDict[_ProbeKey, Sortie] = OrderedDict()

    def probe_sortie(instance: FleetInstance, site_id: str, cell_ids: list[str]) -> Sortie:
        """Вылет-проба с площадки на неё же; последние пробы — из кеша."""
        key = (*instance.key, site_id, tuple(cell_ids))
        if key in probes:
            probes.move_to_end(key)
            return probes[key]
        sortie = make_sortie(instance, cell_ids, site_id, site_id, "probe", 0)
        probes[key] = sortie
        if len(probes) > _PROBE_CACHE_SIZE:
            probes.popitem(last=False)
        return sortie

    def sortie_cost(instance: FleetInstance, site_id: str, cell_ids: list[str]) -> float | None:
        """Длительность вылета — той же моделью, что собирает итоговый вылет.

        None — набор подобластей в один вылет не помещается: не хватает либо
        автономности, либо паспортной протяжённости маршрута (долг №43).
        Оба предела проверяет и краш-тест, независимо от этой стадии.
        """
        probe = probe_sortie(instance, site_id, cell_ids)
        if probe.duration_s > endurance_limit_s(instance.vehicle):
            return None
        if exceeds_range(probe, instance.vehicle):
            return None
        return probe.duration_s

    def survey_time(cell_id: str, vehicle_id: str) -> float:
        """Время галсов подобласти бортом, секунды — нижняя граница вылета."""
        strips = oracle.get(cell_id, vehicle_id).strips
        return sum(strip.length_m / strip.speed_mps for strip in strips)

    def ends_of(cell_id: str, vehicle_id: str) -> CellEnds:
        """Вход и выход змейки подобласти у модели борта, метры."""
        return cell_ends(oracle.get(cell_id, vehicle_id))

    by_key = {instance.key: instance for instance in instances}

    def realize(allocation: Allocation) -> tuple[CoverageSet, list[Sortie], Schedule]:
        """Покрытие, итоговые вылеты и расписание распределения."""
        coverage = oracle.coverage_set(allocation)
        sorties = [
            make_sortie(
                by_key[(assignment.vehicle_id, assignment.vehicle_instance)],
                assignment.cell_ids,
                assignment.takeoff_site_id,
                assignment.landing_site_id,
                assignment.sortie_id,
                assignment.trip_index,
            )
            for assignment in allocation.assignments
        ]
        # Эвристика ставила вылеты в расписание сама, когда собирала, —
        # расписание повторяет её порядок и получает те же старты. Baseline
        # расписания не видит: у него первые вылеты всех бортов, потом вторые.
        sorties, schedule = build_schedule(
            sorties, vehicles, work_area, scenario, coverage, keep_order=allocator != "baseline"
        )
        return coverage, sorties, schedule

    primary_sites = {site.id: work_area.site(site.id) for site in scenario.primary_sites()}
    if allocator == "baseline":
        allocation = build_baseline_allocation(
            scenario,
            work_area.crs,
            decomposition.cells,
            instances,
            primary_sites,
            oracle.serviceable,
            sortie_cost,
        )
        coverage, sorties, schedule = realize(allocation)
    else:
        # Эвристика хаотична: пробуются все стратегии, берётся лучший план по
        # критерию режима (`GreedyStrategy`). В режиме «минимум срока» каждая
        # ещё и под несколько целевых сроков — долей срока своего плана без
        # цели (`ALLOCATE_TARGET_FACTORS`); поиск доводит лучший вариант.
        def placer_for() -> BookPlacer:
            return BookPlacer(
                ScheduleBook(work_area.zones, work_area.daylight),
                probe_sortie,
                oracle,
                survey_plan,
                work_area.crs,
            )

        def greedy(
            strategy: GreedyStrategy, target_s: float | None, basis: Scenario = scenario
        ) -> _Variant:
            candidate = build_greedy_allocation(
                basis,
                work_area.crs,
                decomposition.cells,
                instances,
                primary_sites,
                oracle.serviceable,
                sortie_cost,
                survey_plan,
                survey_time,
                placer_for(),
                ends_of,
                strategy,
                target_s,
            )
            return (candidate, *realize(candidate))

        def improve(variant: _Variant, basis: Scenario = scenario) -> _Variant:
            """Довести вариант локальным поиском по критерию режима `basis`."""
            if allocator != "local_search":
                return variant
            improved = improve_allocation(
                variant[0],
                basis,
                instances,
                decomposition.cells,
                oracle.serviceable,
                sortie_cost,
                SortieBounds(survey_plan, survey_time, decomposition.cells, primary_sites),
                placer_for(),
                primary_sites,
                ends_of,
            )
            if improved.assignments == variant[0].assignments:
                return (improved, *variant[1:])
            return (improved, *realize(improved))

        def key_of(
            item: tuple[GreedyStrategy, Allocation, CoverageSet, list[Sortie], Schedule],
        ) -> tuple[bool, bool, float, float]:
            return _plan_key(scenario, item[1], item[3])

        def best_plan(strategy: GreedyStrategy, basis: Scenario) -> _Variant:
            """Лучший план стратегии по критерию режима `basis`, доведённый поиском.

            Эвристика — без цели и, в режиме «минимум срока», под целевые
            сроки. Поиск доводит вариант без цели и лучший из целевых, если тот
            лучше: лучший до поиска не всегда лучший после (`medium`: 1,025 ч
            против 1,067 ч).
            """

            def key(variant: _Variant) -> tuple[bool, bool, float, float]:
                return _plan_key(basis, variant[0], variant[2])

            plain = greedy(strategy, None, basis)
            candidates = [plain]
            if targets and basis.mode is OptimizationMode.MIN_MAKESPAN and plain[2]:
                makespan_s = max(sortie.end_time_s() for sortie in plain[2])
                aimed = min(
                    (
                        greedy(strategy, makespan_s * factor, basis)
                        for factor in assumptions.ALLOCATE_TARGET_FACTORS
                    ),
                    key=key,
                )
                if key(aimed) < key(plain):
                    candidates.append(aimed)
            return min((improve(variant, basis) for variant in candidates), key=key)

        tried: list[tuple[GreedyStrategy, Allocation, CoverageSet, list[Sortie], Schedule]] = []
        for strategy in STRATEGIES:
            tried.append((strategy, *best_plan(strategy, scenario)))

        # «Минимум ресурса при сроке» (№23, №52): эвристика ресурса занимает
        # самые производительные борта до срока и может не успеть там, где
        # план «минимум срока» успевает. Тогда пробуется он, а поиск по
        # критерию ресурса сливает его вылеты и убирает лишние борта, не
        # выходя за срок.
        if scenario.mode is OptimizationMode.MIN_RESOURCE and scenario.deadline_s is not None:
            incomplete, late, *_rest = key_of(min(tried, key=key_of))
            if incomplete or late:
                fast = scenario.model_copy(update={"mode": OptimizationMode.MIN_MAKESPAN})
                for strategy in STRATEGIES:
                    fallback = replace(strategy, name=f"{strategy.name}, от плана по сроку")
                    tried.append((fallback, *improve(best_plan(strategy, fast))))

        best = min(range(len(tried)), key=lambda index: (key_of(tried[index]), index))
        _strategy, allocation, coverage, sorties, schedule = tried[best]
        if len(tried) > 1:
            assembler.warnings.append(_strategy_note(scenario, tried, best))
    assembler.allocation = allocation
    assembler.coverage = coverage
    schedule.warnings[:0] = level_warnings
    clock.lap("allocate")

    report = validate_plan(scenario, work_area, decomposition, coverage, sorties, vehicles)
    assembler.warnings.extend(report.warnings)
    clock.lap("validate")

    reasons = [*_errors(allocation.warnings), *_errors(schedule.warnings), *report.violations]
    return assembler.finish(reasons, sorties, schedule)


__all__ = [
    "ALLOCATORS",
    "PlanningRun",
    "STUB_STAGES",
    "check_scenario",
    "resolve_fleet",
    "run_pipeline",
]
