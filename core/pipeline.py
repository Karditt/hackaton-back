"""Сборка конвейера по ответам заказчика (docs/dev/PROJECT_BRIEF.md §2).

    Scenario → SurveyPlan → Decomposition → Allocation → Coverage
             → вылеты → Schedule → Validation → Mission → Files

Область сначала делится на подобласти, подобласти распределяются по вылетам,
и только потом каждый борт строит галсы в своей подобласти (заказчик, №27,
№46). Итог — полный план или отчёт о невыполнимости: частичных планов нет
(инвариант 10), план с нарушением жёсткого правила не выдаётся (инвариант 9).

Конвейер знает порядок стадий и больше ничего: ни геометрии, ни физики, ни
оптимизации здесь нет и быть не должно. Стадия, работающая на заглушке,
объявляет себя в `Mission.stub_stages` и предупреждениях (docs/dev/AGENT.md §5).

Варианты распределения — стратегии эвристики, целевые сроки, доводка
поиском — друг от друга не зависят и на больших сценариях считаются в
отдельных процессах (долг №62, `_Workshop`). Процесс повторяет стадии до
распределения сам: они детерминированы, и план от числа процессов не
зависит.
"""

from __future__ import annotations

import multiprocessing
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from shapely.ops import unary_union

from core import assumptions, text
from core.advise import advice_note, diagnose, verify_recommendations
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
    FleetUnit,
    MetricPoint,
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

    @property
    def names(self) -> dict[str, str]:
        """Название модели по идентификатору — подписи бортов в выгрузке."""
        return {vehicle.id: vehicle.name for vehicle in self.catalog}


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
        f"{vehicles[model].name} — до {text.num(share * 100)}% межсервисного интервала на борт"
        for model, share in sorted(worst.items())
    ]
    tail = (
        f"; для {', '.join(sorted(vehicles[model].name for model in missing))} норматив не задан"
        if missing
        else ""
    )
    return PlanWarning(
        code="plan.service_wear",
        severity=Severity.INFO,
        stage=Stage.ALLOCATE,
        message=(
            "Износ по нормативам ТО (Геоскан 201 и 401 — каждые 80 полётов, Геоскан 801 — "
            "каждые 160 ч): " + "; ".join(parts) + tail
        ),
        context={model: f"{share:.4f}" for model, share in worst.items()},
    )


#: Строка состава: модель, нагрузка, площадка, предел вылетов.
_UnitKey = tuple[str, str | None, str | None, int | None]


def _fleet_split(
    scenario: Scenario, survey_plan: SurveyPlan, sorties: list[Sortie]
) -> tuple[list[FleetUnit], list[FleetUnit], list[FleetUnit]]:
    """Состав плана из пула (заказчик, №48): кто в работе, кто на земле.

    Борта пула нумеруются так же, как в `expand_fleet`: по строкам флота,
    отдельно у каждой модели, только годные для съёмки. Площадка борта в
    работе — площадка его первого взлёта.

    Returns:
        Три списка строк состава: в работе, на земле без надобности, на
        земле — не годятся для этой съёмки (нет подходящей нагрузки).
    """
    first_site: dict[tuple[str, int], str] = {}
    for sortie in sorted(sorties, key=lambda item: (item.start_time_s, item.id)):
        first_site.setdefault((sortie.vehicle_id, sortie.vehicle_instance), sortie.takeoff_site_id)
    payloads = {params.vehicle_id: params.payload_id for params in survey_plan.per_vehicle}
    groups: tuple[dict[_UnitKey, int], dict[_UnitKey, int], dict[_UnitKey, int]] = ({}, {}, {})
    counters: dict[str, int] = {}
    for unit in scenario.fleet:
        for _ in range(unit.count):
            key: _UnitKey
            if unit.vehicle_id not in payloads:
                bucket = groups[2]
                key = (unit.vehicle_id, unit.payload_id, unit.site_id, unit.battery_sets)
            else:
                index = counters.get(unit.vehicle_id, 0)
                counters[unit.vehicle_id] = index + 1
                site_id = first_site.get((unit.vehicle_id, index))
                bucket = groups[0] if site_id is not None else groups[1]
                key = (
                    unit.vehicle_id,
                    payloads[unit.vehicle_id],
                    site_id or unit.site_id,
                    unit.battery_sets,
                )
            bucket[key] = bucket.get(key, 0) + 1

    def rows(group: dict[_UnitKey, int]) -> list[FleetUnit]:
        return [
            FleetUnit(
                vehicle_id=vehicle_id,
                count=count,
                payload_id=payload_id,
                site_id=site_id,
                battery_sets=battery_sets,
            )
            for (vehicle_id, payload_id, site_id, battery_sets), count in group.items()
        ]

    return rows(groups[0]), rows(groups[1]), rows(groups[2])


def _idle_note(
    used: list[FleetUnit],
    spare: list[FleetUnit],
    unfit: list[FleetUnit],
    vehicles: dict[str, Vehicle],
) -> PlanWarning | None:
    """Сколько бортов пула в работе и кто остался на земле — если остался кто-то."""
    if not spare and not unfit:
        return None

    def listing(units: list[FleetUnit]) -> str:
        counts: dict[str, int] = {}
        for unit in units:
            counts[unit.vehicle_id] = counts.get(unit.vehicle_id, 0) + unit.count
        return ", ".join(f"{count} × {vehicles[model].name}" for model, count in counts.items())

    working = sum(unit.count for unit in used)
    pool = working + sum(unit.count for unit in [*spare, *unfit])
    parts = []
    if spare:
        parts.append(f"не понадобились плану — {listing(spare)}")
    if unfit:
        parts.append(f"не годятся для этой съёмки — {listing(unfit)}")
    return PlanWarning(
        code="plan.fleet_idle",
        severity=Severity.INFO,
        stage=Stage.ALLOCATE,
        message=f"В работе {working} из {pool} бортов пула; на земле: " + "; ".join(parts),
        context={"used": str(working), "pool": str(pool)},
    )


def _errors(warnings: list[PlanWarning]) -> list[PlanWarning]:
    return [warning for warning in warnings if warning.severity is Severity.ERROR]


#: Ключ сравнения планов по критерию режима (`criterion_key`).
_Key = tuple[bool, bool, float, float]


def _plan_key(scenario: Scenario, allocation: Allocation, sorties: list[Sortie]) -> _Key:
    """Ключ сравнения планов разных стратегий по критерию режима (`criterion_key`)."""
    return criterion_key(
        scenario.mode,
        scenario.deadline_s,
        max((sortie.end_time_s() for sortie in sorties), default=0.0),
        resource_s(sum(sortie.duration_s for sortie in sorties), len(sorties)),
        complete=not allocation.unassigned_cell_ids and bool(sorties),
    )


#: Вариант распределения вместе с его покрытием, вылетами и расписанием.
_Variant = tuple[Allocation, CoverageSet, list[Sortie], Schedule]

#: Стратегия эвристики и лучший её вариант.
_Tried = tuple[GreedyStrategy, Allocation, CoverageSet, list[Sortie], Schedule]


def _strategy_note(scenario: Scenario, tried: list[_Tried], best: int) -> PlanWarning:
    """Какие стратегии распределения пробовались и какая взята."""
    parts = []
    for strategy, allocation, _coverage, sorties, _schedule in tried:
        if allocation.unassigned_cell_ids or not sorties:
            parts.append(f"«{strategy.name}» — задание не выполняет")
            continue
        makespan_s = max(sortie.end_time_s() for sortie in sorties)
        flight_s = sum(sortie.duration_s for sortie in sorties)
        parts.append(
            f"«{strategy.name}» — срок {text.duration_text(makespan_s)}, налёт "
            f"{text.duration_text(flight_s)}"
        )
    return PlanWarning(
        code="allocate.strategies",
        severity=Severity.INFO,
        stage=Stage.ALLOCATE,
        message=(
            "Сравнены способы распределения работ: "
            + "; ".join(parts)
            + f". Выбран «{tried[best][0].name}» — лучший по критерию "
            f"«{text.MODE_LABELS[scenario.mode]}»"
        ),
        context={"strategy": tried[best][0].name},
    )


#: Вылет-проба: борт, площадка (взлёт и посадка), подобласти по порядку.
_ProbeKey = tuple[str, int, str, tuple[str, ...]]

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
    base_catalog: VehicleCatalog | None = None
    """Каталог, переданный конвейеру, — для проверочных расчётов советов."""
    verify: bool = True
    """Проверять ли советы невыполнимого плана расчётом (`verify_recommendations`)."""

    def _plan(self, scenario: Scenario) -> Mission:
        """Проверочный расчёт совета: без целевых сроков и без проверки своих советов."""
        return run_pipeline(scenario, self.base_catalog, targets=False, verify=False).mission

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
        used: list[FleetUnit] = []
        idle: list[FleetUnit] = []
        if not feasible:
            recommendations = diagnose(
                scenario,
                self.work_area,
                self.survey_plan,
                self.decomposition,
                reasons,
                self.vehicles,
            )
            if self.verify:
                recommendations = verify_recommendations(
                    scenario,
                    self.work_area,
                    self.survey_plan,
                    self.vehicles,
                    recommendations,
                    self._plan,
                )
            mission_warnings.append(advice_note(self.verify))
        else:
            used, spare, unfit = _fleet_split(scenario, self.survey_plan, sorties or [])
            idle = [*spare, *unfit]
            for note in (
                _idle_note(used, spare, unfit, self.vehicles),
                _service_note(sorties or [], self.vehicles),
            ):
                if note is not None:
                    mission_warnings.append(note)

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
            fleet_used=used,
            fleet_idle=idle,
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


def _front_stages(
    scenario: Scenario, catalog: VehicleCatalog | None, clock: _Clock, verify: bool = True
) -> tuple[_Assembler, PlanningRun | None]:
    """Стадии до распределения: геометрия, съёмка, маска, подобласти.

    Детерминированы: при одном сценарии и каталоге в любом процессе дают одно
    и то же — поэтому процесс, считающий варианты распределения, повторяет их
    сам, а не получает готовыми.

    Returns:
        Сборщик итога (с подобластями, если до них дошло) и итог, если
        считать дальше не из чего; иначе None.
    """
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
        base_catalog=catalog,
        verify=verify,
    )
    if not survey_plan.per_vehicle:
        return assembler, assembler.finish(_errors(survey_plan.warnings), stub_stages=())

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
        return assembler, assembler.finish([reason], stub_stages=())
    assembler.warnings.extend(mask_warnings)

    assembler.decomposition = decompose(
        work_area, mask, survey_plan, vehicles, [site.id for site in scenario.primary_sites()]
    )
    clock.lap("decompose")
    return assembler, None


class _Workshop:
    """Всё, из чего распределение собирает и оценивает варианты плана.

    Строится из стадий до распределения — в процессе конвейера и в каждом
    процессе, который считает варианты (`_worker_init`). Кеши проб, покрытий и
    маршрутов на результат не влияют — только на число повторных сборок.
    """

    def __init__(self, scenario: Scenario, assembler: _Assembler, allocator: str) -> None:
        decomposition = assembler.decomposition
        assert decomposition is not None
        self.scenario = scenario
        self.allocator = allocator
        self.vehicles = assembler.vehicles
        self.work_area = assembler.work_area
        self.survey_plan = assembler.survey_plan
        self.decomposition = decomposition
        usable = [params.vehicle_id for params in self.survey_plan.per_vehicle]
        self.instances = expand_fleet(scenario, self.vehicles, usable)
        self.levels, self.level_warnings = assign_transit_levels(
            self.instances,
            self.survey_plan,
            self.work_area.corridor_max_m,
            [item.safe_altitude_m for item in self.work_area.obstacles],
        )
        self.oracle = CoverageOracle(
            decomposition, self.survey_plan, self.vehicles, self.work_area, scenario.wind
        )
        self.router = make_router(self.work_area)
        self.hops = SurveyHopPolicy(
            decomposition.cells, self.survey_plan, self.vehicles, self.work_area
        )
        self.slope = SlopeClearance(self.work_area)
        self.primary_sites: dict[str, MetricPoint] = {
            site.id: self.work_area.site(site.id) for site in scenario.primary_sites()
        }
        self.by_key = {instance.key: instance for instance in self.instances}
        self._probes: OrderedDict[_ProbeKey, Sortie] = OrderedDict()

    def basis(self, mode: OptimizationMode) -> Scenario:
        """Сценарий с режимом `mode` — критерий, по которому сравниваются варианты."""
        if mode is self.scenario.mode:
            return self.scenario
        return self.scenario.model_copy(update={"mode": mode})

    def make_sortie(
        self,
        instance: FleetInstance,
        cell_ids: list[str],
        takeoff_site_id: str,
        landing_site_id: str,
        sortie_id: str,
        trip_index: int,
    ) -> Sortie:
        """Вылет борта по подобластям — моделью полёта (E4)."""
        params = self.survey_plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        own_area = unary_union([self.oracle.cell_geometry(cell_id) for cell_id in cell_ids])
        return build_sortie(
            sortie_id=sortie_id,
            vehicle=instance.vehicle,
            params=params,
            takeoff_site_id=takeoff_site_id,
            takeoff_site=self.work_area.site(takeoff_site_id),
            landing_site_id=landing_site_id,
            landing_site=self.work_area.site(landing_site_id),
            cells=[self.oracle.get(cell_id, instance.vehicle.id) for cell_id in cell_ids],
            transit_altitude_m=self.levels[instance.key],
            router=self.router,
            own_area=own_area,
            vehicle_instance=instance.index,
            trip_index=trip_index,
            survey_hop=self.hops.check(instance.vehicle.id, set(cell_ids), own_area),
            slope_check=self.slope,
        )

    def probe_sortie(self, instance: FleetInstance, site_id: str, cell_ids: list[str]) -> Sortie:
        """Вылет-проба с площадки на неё же; последние пробы — из кеша."""
        key = (*instance.key, site_id, tuple(cell_ids))
        if key in self._probes:
            self._probes.move_to_end(key)
            return self._probes[key]
        sortie = self.make_sortie(instance, cell_ids, site_id, site_id, "probe", 0)
        self._probes[key] = sortie
        if len(self._probes) > _PROBE_CACHE_SIZE:
            self._probes.popitem(last=False)
        return sortie

    def sortie_cost(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str]
    ) -> float | None:
        """Длительность вылета — той же моделью, что собирает итоговый вылет.

        None — набор подобластей в один вылет не помещается: не хватает либо
        автономности, либо паспортной протяжённости маршрута (долг №43).
        Оба предела проверяет и краш-тест, независимо от этой стадии.
        """
        probe = self.probe_sortie(instance, site_id, cell_ids)
        if probe.duration_s > endurance_limit_s(instance.vehicle):
            return None
        if exceeds_range(probe, instance.vehicle):
            return None
        return probe.duration_s

    def survey_time(self, cell_id: str, vehicle_id: str) -> float:
        """Время галсов подобласти бортом, секунды — нижняя граница вылета."""
        strips = self.oracle.get(cell_id, vehicle_id).strips
        return sum(strip.length_m / strip.speed_mps for strip in strips)

    def ends_of(self, cell_id: str, vehicle_id: str) -> CellEnds:
        """Вход и выход змейки подобласти у модели борта, метры."""
        return cell_ends(self.oracle.get(cell_id, vehicle_id))

    def realize(self, allocation: Allocation) -> tuple[CoverageSet, list[Sortie], Schedule]:
        """Покрытие, итоговые вылеты и расписание распределения."""
        coverage = self.oracle.coverage_set(allocation)
        sorties = [
            self.make_sortie(
                self.by_key[(assignment.vehicle_id, assignment.vehicle_instance)],
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
            sorties,
            self.vehicles,
            self.work_area,
            self.scenario,
            coverage,
            keep_order=self.allocator != "baseline",
        )
        return coverage, sorties, schedule

    def variant_of(self, allocation: Allocation) -> _Variant:
        """Распределение вместе с его покрытием, вылетами и расписанием."""
        return (allocation, *self.realize(allocation))

    def baseline(self) -> Allocation:
        """Равномерное деление — точка сравнения (инвариант 8)."""
        return build_baseline_allocation(
            self.scenario,
            self.work_area.crs,
            self.decomposition.cells,
            self.instances,
            self.primary_sites,
            self.oracle.serviceable,
            self.sortie_cost,
        )

    def placer(self) -> BookPlacer:
        """Пустая книга расписания для распределения (долг №54)."""
        return BookPlacer(
            ScheduleBook(self.work_area.zones, self.work_area.daylight),
            self.probe_sortie,
            self.oracle,
            self.survey_plan,
            self.work_area.crs,
        )

    def greedy(
        self, strategy: GreedyStrategy, target_s: float | None, basis: Scenario
    ) -> tuple[Allocation, _Key, float]:
        """Эвристика и её оценка по книге расписания, без сборки плана.

        Расписание ставит итоговые вылеты в том же порядке, что эвристика
        ставила их в книгу, и получает те же старты (долг №54) — поэтому
        срок и налёт из книги равны сроку и налёту плана, а собирать план
        ради сравнения вариантов незачем (долг №62).

        Returns:
            Распределение, ключ критерия режима `basis` и срок, секунды.
        """
        placer = self.placer()
        candidate = build_greedy_allocation(
            basis,
            self.work_area.crs,
            self.decomposition.cells,
            self.instances,
            self.primary_sites,
            self.oracle.serviceable,
            self.sortie_cost,
            self.survey_plan,
            self.survey_time,
            placer,
            self.ends_of,
            strategy,
            target_s,
        )
        end_s, flight_s, count = placer.totals()
        key = criterion_key(
            basis.mode,
            basis.deadline_s,
            end_s,
            resource_s(flight_s, count),
            complete=not candidate.unassigned_cell_ids and count > 0,
        )
        return candidate, key, end_s

    def improve(self, allocation: Allocation, basis: Scenario) -> Allocation:
        """Довести распределение локальным поиском по критерию режима `basis`."""
        if self.allocator != "local_search":
            return allocation
        return improve_allocation(
            allocation,
            basis,
            self.instances,
            self.decomposition.cells,
            self.oracle.serviceable,
            self.sortie_cost,
            SortieBounds(
                self.survey_plan, self.survey_time, self.decomposition.cells, self.primary_sites
            ),
            self.placer(),
            self.primary_sites,
            self.ends_of,
        )


# --------------------------------------------------------------------------
# Задачи распределения: одинаково считаются в процессе конвейера и в пуле
# --------------------------------------------------------------------------


def _task_greedy(
    workshop: _Workshop, strategy_index: int, target_s: float | None, mode: OptimizationMode
) -> tuple[Allocation, _Key, float]:
    """Эвристика стратегии `STRATEGIES[strategy_index]` под целевой срок (или без)."""
    return workshop.greedy(STRATEGIES[strategy_index], target_s, workshop.basis(mode))


def _task_finish(workshop: _Workshop, allocation: Allocation, mode: OptimizationMode) -> _Variant:
    """Довести распределение поиском по критерию режима `mode` и собрать план."""
    return workshop.variant_of(workshop.improve(allocation, workshop.basis(mode)))


#: Состояние процесса пула: мастерская, собранная при его запуске. Живёт, пока
#: жив процесс; конвейер в своём процессе глобальным состоянием не пользуется.
_WORKER: _Workshop | None = None


def _worker_init(scenario: Scenario, catalog: VehicleCatalog | None, allocator: str) -> None:
    """Запуск процесса пула: повторить стадии до распределения."""
    global _WORKER
    assembler, early = _front_stages(scenario, catalog, _Clock())
    assert early is None, "процессу пула нечего распределять"
    _WORKER = _Workshop(scenario, assembler, allocator)


def _in_worker(task: Callable[..., Any], call: tuple[Any, ...]) -> Any:
    """Выполнить задачу распределения в процессе пула."""
    assert _WORKER is not None
    return task(_WORKER, *call)


class _Runner(Protocol):
    """Исполнитель задач распределения: здесь же или в пуле процессов."""

    def map(self, task: Callable[..., Any], calls: Sequence[tuple[Any, ...]]) -> list[Any]:
        """Результаты задачи на каждом наборе аргументов, в том же порядке."""
        ...


@dataclass
class _InProcess:
    """Задачи — по очереди, в процессе конвейера."""

    workshop: _Workshop

    def map(self, task: Callable[..., Any], calls: Sequence[tuple[Any, ...]]) -> list[Any]:
        return [task(self.workshop, *call) for call in calls]


@dataclass
class _InPool:
    """Задачи — параллельно, в процессах пула; порядок результатов — порядок задач."""

    executor: ProcessPoolExecutor

    def map(self, task: Callable[..., Any], calls: Sequence[tuple[Any, ...]]) -> list[Any]:
        futures = [self.executor.submit(_in_worker, task, call) for call in calls]
        return [future.result() for future in futures]


def _worker_count(cells: int, workers: int | None) -> int:
    """Сколько процессов считают варианты распределения; 1 — в процессе конвейера."""
    if workers is not None:
        return max(1, workers)
    if cells < assumptions.PLANNING_PARALLEL_MIN_CELLS:
        return 1
    return max(1, min(assumptions.PLANNING_WORKERS_MAX, os.cpu_count() or 1))


def _best_plans(
    runner: _Runner, scenario: Scenario, mode: OptimizationMode, targets: bool
) -> list[_Variant]:
    """Лучший план каждой стратегии по критерию режима `mode`, доведённый поиском.

    Эвристика — без цели и, в режиме «минимум срока», под целевые сроки
    (`ALLOCATE_TARGET_FACTORS` — доли срока своего плана без цели). Поиск
    доводит вариант без цели и лучший из целевых, если тот лучше: лучший до
    поиска не всегда лучший после (`medium`: 1,025 ч против 1,067 ч).

    Returns:
        По варианту на стратегию, в порядке `STRATEGIES`.
    """
    basis = scenario if mode is scenario.mode else scenario.model_copy(update={"mode": mode})
    plains = runner.map(_task_greedy, [(index, None, mode) for index in range(len(STRATEGIES))])
    candidates: list[list[Allocation]] = [[plain] for plain, _key, _end_s in plains]
    if targets and mode is OptimizationMode.MIN_MAKESPAN:
        calls = [
            (index, end_s * factor, mode)
            for index, (plain, _key, end_s) in enumerate(plains)
            if plain.assignments
            for factor in assumptions.ALLOCATE_TARGET_FACTORS
        ]
        aimed: dict[int, list[tuple[Allocation, _Key, float]]] = {}
        for call, result in zip(calls, runner.map(_task_greedy, calls), strict=True):
            aimed.setdefault(call[0], []).append(result)
        for index, results in aimed.items():
            best, best_key, _end_s = min(results, key=lambda item: item[1])
            if best_key < plains[index][1]:
                candidates[index].append(best)

    finished = runner.map(
        _task_finish, [(allocation, mode) for group in candidates for allocation in group]
    )
    plans: list[_Variant] = []
    for group in candidates:
        variants, finished = finished[: len(group)], finished[len(group) :]
        plans.append(min(variants, key=lambda variant: _plan_key(basis, variant[0], variant[2])))
    return plans


def _allocate(runner: _Runner, scenario: Scenario, targets: bool) -> list[_Tried]:
    """Варианты распределения всех стратегий — эвристика хаотична (`GreedyStrategy`)."""
    tried: list[_Tried] = [
        (strategy, *plan)
        for strategy, plan in zip(
            STRATEGIES, _best_plans(runner, scenario, scenario.mode, targets), strict=True
        )
    ]

    # «Минимум ресурса при сроке» (№23, №52): эвристика ресурса занимает
    # самые производительные борта до срока и может не успеть там, где план
    # «минимум срока» успевает, — или успеть, но дороже него (`simple` к сроку
    # 1,00 ч: ресурс 2,06 ч против 2,03 ч у плана по сроку). Поэтому при сроке
    # пробуется и план «минимум срока», а поиск по критерию ресурса сливает его
    # вылеты и убирает лишние борта, не выходя за срок: итог не дороже плана
    # по сроку, если тот успевает.
    if scenario.mode is OptimizationMode.MIN_RESOURCE and scenario.deadline_s is not None:
        quickest = _best_plans(runner, scenario, OptimizationMode.MIN_MAKESPAN, targets)
        finished = runner.map(_task_finish, [(plan[0], scenario.mode) for plan in quickest])
        for strategy, plan in zip(STRATEGIES, finished, strict=True):
            fallback = replace(strategy, name=f"{strategy.name}, от плана по сроку")
            tried.append((fallback, *plan))
    return tried


def run_pipeline(
    scenario: Scenario,
    catalog: VehicleCatalog | None = None,
    mode: OptimizationMode | None = None,
    alpha: float | None = None,
    seed: int | None = None,
    allocator: str = ALLOCATORS[0],
    targets: bool = True,
    workers: int | None = None,
    verify: bool = True,
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
        workers: сколько процессов считают варианты распределения. None —
            по размеру сценария и числу ядер (`PLANNING_PARALLEL_MIN_CELLS`,
            `PLANNING_WORKERS_MAX`); 1 — всё в этом процессе. На план не влияет.
        verify: проверять ли советы невыполнимого плана расчётом — это ещё
            прогоны конвейера со сценарием, поправленным по совету. False —
            для множества прогонов (подсказки, фронт Парето, сравнение).

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
    assembler, early = _front_stages(scenario, catalog, clock, verify)
    if early is not None:
        return early
    workshop = _Workshop(scenario, assembler, allocator)

    if allocator == "baseline":
        allocation = workshop.baseline()
        coverage, sorties, schedule = workshop.realize(allocation)
    else:
        count = _worker_count(len(workshop.decomposition.cells), workers)
        tried: list[_Tried] | None = None
        if count > 1:
            # Процессы пула строят мастерскую сами — «spawn» и на Linux: форк
            # процесса с потоками (API считает в пуле потоков) небезопасен.
            try:
                with ProcessPoolExecutor(
                    max_workers=count,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=_worker_init,
                    initargs=(scenario, catalog, allocator),
                ) as executor:
                    tried = _allocate(_InPool(executor), scenario, targets)
            except (BrokenProcessPool, OSError):
                # Процесс не запустился или погиб (память) — считаем здесь:
                # план тот же, только дольше.
                tried = None
        if tried is None:
            tried = _allocate(_InProcess(workshop), scenario, targets)

        best = min(
            range(len(tried)),
            key=lambda index: (_plan_key(scenario, tried[index][1], tried[index][3]), index),
        )
        _strategy, allocation, coverage, sorties, schedule = tried[best]
        if len(tried) > 1:
            assembler.warnings.append(_strategy_note(scenario, tried, best))
    assembler.allocation = allocation
    assembler.coverage = coverage
    schedule.warnings[:0] = workshop.level_warnings
    clock.lap("allocate")

    report = validate_plan(
        scenario, workshop.work_area, workshop.decomposition, coverage, sorties, workshop.vehicles
    )
    assembler.warnings.extend(report.warnings)
    clock.lap("validate")

    reasons = [*_errors(allocation.warnings), *_errors(schedule.warnings), *report.violations]
    return assembler.finish(reasons, sorties, schedule)


def restore_run(
    scenario: Scenario, result: PipelineResult, catalog: VehicleCatalog | None = None
) -> PlanningRun:
    """Собрать прогон из сохранённых сценария и результата — без расчёта.

    Нужен, чтобы готовый план пережил перезапуск API: выгрузке, кроме
    результата, нужны рабочая область (проекция обратно в WGS84) и каталог.
    Рабочая область строится заново — она детерминирована сценарием.

    Args:
        scenario: сценарий, по которому считался результат (после переопределений).
        result: сохранённый результат конвейера.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.

    Raises:
        GeometryError, ScenarioError, CatalogError: сценарий больше не читается.
    """
    vehicle_catalog = scenario_catalog(scenario, catalog)
    return PlanningRun(
        scenario=scenario,
        result=result,
        work_area=build_work_area(scenario),
        catalog=vehicle_catalog,
        vehicles=resolve_fleet(scenario, vehicle_catalog),
    )


__all__ = [
    "ALLOCATORS",
    "PlanningRun",
    "STUB_STAGES",
    "check_scenario",
    "resolve_fleet",
    "restore_run",
    "run_pipeline",
]
