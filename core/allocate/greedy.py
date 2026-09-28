"""Конструктивная эвристика распределения (E6, шаг 2): «кто раньше освободится».

Baseline (`baseline.py`) делит подобласти поровну по **площади** и даёт каждому
борту одну площадку — ближайшую к его куску. На разнотипном флоте это
разбалансирует план: самолёт снимает впятеро быстрее коптера, а получает
столько же; на данных заказчика у baseline простаивают целые площадки и
борта (docs/current_state.md §2). Здесь делится **время**.

1. **Площадки.** Борт, приписанный сценарием, стоит на своей. Площадка, у
   которой есть работа, досягаемая только с неё, получает подходящий борт
   первой. Остальные короткие борта расставляются по одному, начиная с самых
   «коротких»: туда, где борт снимет больше всего работы, до которой долетает
   и которую не заберут уже стоящие там борта. Дальнобойные (достают любую
   подобласть от ближайшей к ней площадки) — пропорционально работе площадок,
   по методу д'Ондта (долг №60). Работа площадки — подобласти, для которых она
   ближайшая.
2. **Очередь.** Следующий вылет получает борт, который освободится раньше
   всех (режим «минимум срока»), либо самый производительный из тех, кто
   ещё укладывается в дедлайн (режим «минимум ресурса»).
3. **Вылет.** Сначала — своя зона: подобласти, для которых площадка борта
   ближайшая из площадок с бортами той же модели (иначе самолёт с южной
   площадки летит к подобластям у восточной, а восточные — к южным); в
   стратегии «секторы бортов» — ещё и свой сектор по азимуту, поровну между
   бортами модели на площадке (`GreedyStrategy`; конвейер пробует обе
   стратегии и берёт лучший план). В зоне
   затравка — самая дальняя подобласть, которую борт снимет за вылет:
   дальние уходят тем, кто до них долетает, пока ближние остаются для всех.
   У самого короткого борта флота — наоборот, ближняя (долг №59). Дальше
   вылет добирается ближайшими к последней добавленной подобластями, пока
   помещается в заряд и запас хода; добор кончился — подобласти
   переставляются (`core.allocate.order`, долг №58), и если вылет стал
   короче, добор продолжается.

4. **Расписание видно сразу** (долг №54). Собранный вылет ставится в книгу
   расписания (`SortiePlacer`) — ту же, что потом строит расписание, — и
   эвристика узнаёт его настоящий старт: разведение с уже поставленными
   бортами может сдвинуть его на часы (самолёты на одной высоте съёмки в
   соседних подобластях). Если старт сдвинут, вылет собирается заново без
   подобластей, из-за которых сдвиг, и так до `ALLOCATE_DELAY_RETRIES` раз;
   берётся вариант, который снимает больше площади за время, занятое бортом
   от готовности до посадки. Борт освобождается по настоящей посадке, а
   очередь «кто раньше освободится» считает уже с задержками.

Время вылета — **та же модель полёта**, что собирает итоговый план
(`SortieCost`, долг №14). Нижняя граница по прямой без обходов отсекает
безнадёжных кандидатов до точной сборки. Случайности нет: одно и то же на
входе — побитово один план.

Невзятые подобласти перечисляются с причиной, как у baseline: частичных
планов нет (инвариант 10).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from shapely.geometry import Point

from core import assumptions
from core.allocate.baseline import ServiceablePredicate, SortieCost, _unassigned_warnings
from core.allocate.fleet import FleetInstance, sortie_id
from core.allocate.order import CellEndsOf, improve_order
from core.flight.cost import endurance_limit_s
from core.geo.project import polygon_to_shapely
from core.schemas import (
    Allocation,
    Cell,
    CrsInfo,
    MetricPoint,
    OptimizationMode,
    PlanWarning,
    Scenario,
    Severity,
    SortieAssignment,
    Stage,
    SurveyPlan,
)

#: Имя алгоритма в отчётах и в сравнении с baseline.
ALGORITHM_NAME = "constructive:earliest_free"

#: Время съёмки подобласти бортом, секунды: сумма галсов на путевой скорости.
#: Нижняя граница — без разворотов, переходов и подлёта.
SurveyTime = Callable[[str, str], float]


class SortiePlacer(Protocol):
    """Книга расписания, которую видит распределение (E14 → E6, долг №54).

    Вылет задаётся бортом, площадкой (взлёт и посадка — на ней) и порядком
    подобластей; время — секунды от начала работ.
    """

    def earliest_start(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str], ready_s: float
    ) -> float | None:
        """Самый ранний взлёт не раньше `ready_s` без сближений с поставленными; None — нет."""
        ...

    def blocking_cells(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str], start_s: float
    ) -> set[str]:
        """Подобласти вылета, из-за которых нельзя взлететь в `start_s`."""
        ...

    def crowded_cells(
        self, instance: FleetInstance, cell_ids: list[str], from_s: float, to_s: float
    ) -> set[str]:
        """Подобласти, у которых в этот промежуток работают другие самолёты на той же высоте."""
        ...

    def commit(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str], start_s: float
    ) -> None:
        """Поставить вылет со взлётом в `start_s`."""
        ...


@dataclass
class _Board:
    """Борт в очереди эвристики."""

    instance: FleetInstance
    site_id: str
    free_at_s: float
    trips: int = 0
    active: bool = True


def _distance(a: MetricPoint, b: MetricPoint) -> float:
    return math.hypot(a.x_m - b.x_m, a.y_m - b.y_m)


class SortieBounds:
    """Нижние границы времени вылета по прямой — чтобы не собирать безнадёжные вылеты."""

    def __init__(
        self,
        survey_plan: SurveyPlan,
        survey_time: SurveyTime,
        cells: list[Cell],
        sites: dict[str, MetricPoint],
    ) -> None:
        self._plan = survey_plan
        self._survey_time = survey_time
        self._shapes = {cell.id: polygon_to_shapely(cell.polygon) for cell in cells}
        self._sites = {site_id: Point(point.x_m, point.y_m) for site_id, point in sites.items()}
        self._gaps: dict[tuple[str, str], float] = {}

    def gap_m(self, site_id: str, cell_id: str) -> float:
        """От площадки до ближайшей точки подобласти, метры.

        Не до центра: заход в подобласть — с её края, и расстояние до центра
        завышало бы нижнюю границу, отсекая посильные подобласти на пределе
        дальности.
        """
        key = (site_id, cell_id)
        if key not in self._gaps:
            self._gaps[key] = float(self._shapes[cell_id].distance(self._sites[site_id]))
        return self._gaps[key]

    def single_floor_s(self, instance: FleetInstance, site_id: str, cell_id: str) -> float:
        """Нижняя граница вылета на одну подобласть с площадки, секунды."""
        return self.overhead_s(instance, self.gap_m(site_id, cell_id)) + self.survey_s(
            instance, cell_id
        )

    def overhead_s(self, instance: FleetInstance, distance_m: float) -> float:
        """Взлёт, посадка, набор и снижение до высоты съёмки, подлёт туда-обратно, секунды.

        Самолёт набирает и теряет высоту на ходу (долг №56) — у него набор и
        снижение не добавляются к подлёту, иначе граница перестала бы быть
        нижней.
        """
        params = self._plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        vertical_s = (
            0.0
            if instance.vehicle.is_fixed_wing()
            else params.altitude_agl_m / assumptions.CLIMB_RATE_MPS
            + params.altitude_agl_m / assumptions.DESCENT_RATE_MPS
        )
        return (
            assumptions.TAKEOFF_TIME_S
            + assumptions.LANDING_TIME_S
            + vertical_s
            + 2.0 * distance_m / params.transit_speed_mps
        )

    def survey_s(self, instance: FleetInstance, cell_id: str) -> float:
        """Время галсов подобласти этим бортом, секунды."""
        return self._survey_time(cell_id, instance.vehicle.id)

    def rate_m2ps(self, instance: FleetInstance) -> float:
        """Производительность съёмки: шаг галсов × путевая скорость, м²/с."""
        params = self._plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        return params.strip_spacing_m * params.survey_speed_mps

    def reach_m(self, instance: FleetInstance) -> float:
        """Дальше этого от площадки борт не долетит даже без съёмки, метры."""
        params = self._plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        spare_s = endurance_limit_s(instance.vehicle) - self.overhead_s(instance, 0.0)
        return max(spare_s, 0.0) / 2.0 * params.transit_speed_mps


def _place_boards(
    cells: list[Cell],
    instances: list[FleetInstance],
    sites: dict[str, MetricPoint],
    serviceable: ServiceablePredicate,
    bounds: SortieBounds,
) -> dict[tuple[str, int], str]:
    """Расставить борта по площадкам (п. 1 докстринга модуля).

    Горизонт — оценка срока снизу: вся площадь на суммарную производительность
    флота. За горизонт борт снимет `производительность × горизонт`; ставится
    он туда, где эта площадь больше всего пойдёт в дело: на площадку, у
    которой ещё осталось работы, до которой он долетает и которую не заберут
    уже стоящие там борта. Работа площадки — подобласти, для которых она
    ближайшая.
    """
    site_ids = list(sites)
    nearest = {
        cell.id: min(site_ids, key=lambda site: _distance(sites[site], cell.centroid))
        for cell in cells
    }
    total_rate = sum(bounds.rate_m2ps(instance) for instance in instances)
    horizon_s = sum(cell.area_m2 for cell in cells) / total_rate if total_rate > 0.0 else 0.0
    taken = {site: 0.0 for site in site_ids}
    placed: dict[tuple[str, int], str] = {}
    for instance in instances:
        if instance.site_id is not None:
            placed[instance.key] = instance.site_id
            taken[instance.site_id] += bounds.rate_m2ps(instance) * horizon_s

    # Первыми — борта с наименьшей дальностью: им площадка важнее всего, а
    # самолёт со своей площадки достанет и чужую работу.
    free = [instance for instance in instances if instance.site_id is None]
    free.sort(key=lambda instance: (bounds.reach_m(instance), -bounds.rate_m2ps(instance)))

    # Работа, досягаемая только с одной площадки (полоса у зоны, которую может
    # снять лишь коптер, и лишь отсюда), получает борт раньше всех: иначе её
    # «перевесит» площадка побогаче, и план станет невыполнимым (долг №53).
    for site, models in _orphan_needs(cells, instances, site_ids, serviceable, bounds):
        if any(
            placed.get(instance.key) == site and instance.vehicle.id in models
            for instance in instances
        ):
            continue
        helper = next((item for item in free if item.vehicle.id in models), None)
        if helper is None:
            continue
        placed[helper.key] = site
        taken[site] += bounds.rate_m2ps(helper) * horizon_s
        free.remove(helper)

    # Дальнобойные борта (достают любую подобласть от ближайшей к ней
    # площадки) расставляются пропорционально работе площадок, как места по
    # методу д'Ондта: площадке достаётся следующий борт, пока её нагрузка на
    # борт больше, чем у других (долг №60). Иначе все самолёты встают туда, где
    # больше всего ближней работы, и летают через всю область к чужим
    # площадкам.
    rangy = [
        instance
        for instance in free
        if all(
            bounds.gap_m(nearest[cell.id], cell.id) <= bounds.reach_m(instance)
            for cell in cells
            if serviceable(cell.id, instance.vehicle.id)
        )
    ]
    if len(site_ids) < 2:
        rangy = []
    free = [instance for instance in free if instance not in rangy]

    for instance in free:
        limit_s = endurance_limit_s(instance.vehicle)
        share = bounds.rate_m2ps(instance) * horizon_s
        left = {}
        for site in site_ids:
            work = sum(
                cell.area_m2
                for cell in cells
                if nearest[cell.id] == site
                and serviceable(cell.id, instance.vehicle.id)
                and bounds.single_floor_s(instance, site, cell.id) <= limit_s
            )
            left[site] = work - taken[site]
        best = max(
            site_ids,
            key=lambda site: (min(max(left[site], 0.0), share), left[site], -site_ids.index(site)),
        )
        placed[instance.key] = best
        taken[best] += share

    for instance in rangy:
        # Короткие борта в нагрузке не учитываются: их доля работы площадки
        # оценивается по скорости съёмки и сильно завышена — на данных заказчика
        # коптер тратит на подлёт две трети вылета, и с их «долей» все самолёты
        # снова вставали на одну площадку.
        load = _site_load(cells, instance, site_ids, nearest, serviceable, bounds)
        count = {site: 0 for site in site_ids}
        for other in instances:
            if other.vehicle.id == instance.vehicle.id and other.key in placed:
                count[placed[other.key]] += 1
        best = max(
            site_ids,
            key=lambda site: (load[site] / (count[site] + 1), -site_ids.index(site)),
        )
        placed[instance.key] = best
    return placed


def _site_load(
    cells: list[Cell],
    instance: FleetInstance,
    site_ids: list[str],
    nearest: dict[str, str],
    serviceable: ServiceablePredicate,
    bounds: SortieBounds,
) -> dict[str, float]:
    """Сколько времени борту этой модели нужно на работу каждой площадки, секунды.

    Работа площадки — подобласти, для которых она ближайшая. Время подобласти —
    галсы, растянутые на подлёт: вылет на расстояние d тратит на съёмку только
    то, что остаётся от заряда после подлёта туда и обратно, поэтому дальняя
    работа дороже ближней.
    """
    limit_s = endurance_limit_s(instance.vehicle)
    load = dict.fromkeys(site_ids, 0.0)
    for cell in cells:
        if not serviceable(cell.id, instance.vehicle.id):
            continue
        site = nearest[cell.id]
        spare_s = limit_s - bounds.overhead_s(instance, bounds.gap_m(site, cell.id))
        if spare_s <= 0.0:
            continue
        load[site] += bounds.survey_s(instance, cell.id) * limit_s / spare_s
    return load


def _orphan_needs(
    cells: list[Cell],
    instances: list[FleetInstance],
    site_ids: list[str],
    serviceable: ServiceablePredicate,
    bounds: SortieBounds,
) -> list[tuple[str, set[str]]]:
    """Площадки с «сиротской» работой и модели, которые могут её снять.

    Подобласть — сирота площадки, если по нижней границе её может снять за
    вылет только борт, стоящий на этой площадке (какой бы модели он ни был).

    Returns:
        Пары «площадка, модели», по убыванию площади сиротской работы.
    """
    sample = {instance.vehicle.id: instance for instance in instances}
    area: dict[str, float] = {}
    models: dict[str, set[str]] = {}
    for cell in cells:
        reachable = {
            (model, site)
            for model, instance in sample.items()
            if serviceable(cell.id, model)
            for site in site_ids
            if bounds.single_floor_s(instance, site, cell.id) <= endurance_limit_s(instance.vehicle)
        }
        homes = {site for _model, site in reachable}
        if len(homes) != 1:
            continue
        home = homes.pop()
        area[home] = area.get(home, 0.0) + cell.area_m2
        models.setdefault(home, set()).update(model for model, _site in reachable)
    order = sorted(area, key=lambda site: (-area[site], site_ids.index(site)))
    return [(site, models[site]) for site in order]


def build_greedy_allocation(
    scenario: Scenario,
    crs: CrsInfo,
    cells: list[Cell],
    instances: list[FleetInstance],
    sites: dict[str, MetricPoint],
    serviceable: ServiceablePredicate,
    cost: SortieCost,
    survey_plan: SurveyPlan,
    survey_time: SurveyTime,
    placer: SortiePlacer | None = None,
    cell_ends: CellEndsOf | None = None,
    strategy: GreedyStrategy | None = None,
) -> Allocation:
    """Распределить подобласти по вылетам: время делится, а не площадь.

    Args:
        scenario: сценарий (режим, дедлайн, межвылетная подготовка).
        crs: рабочая проекция.
        cells: подобласти.
        instances: физические борта.
        sites: штатные площадки в метрах.
        serviceable: пригодность пары «подобласть × модель борта».
        cost: длительность вылета — точная модель полёта — либо None, если
            вылет не помещается в заряд или запас хода.
        survey_plan: параметры съёмки бортов.
        survey_time: время галсов подобласти бортом, секунды.
        placer: книга расписания. None — задержек расписания эвристика не
            видит: борт взлетает, как только готов.
        cell_ends: концы змейки подобласти у модели борта — по ним собранный
            вылет переставляется (`core.allocate.order`). None — порядок облёта
            остаётся порядком добора.
        strategy: как делить работу между бортами одной площадки; None —
            первая из `STRATEGIES`.

    Returns:
        Распределение с невзятыми подобластями и причинами. Вылеты — в том
        порядке, в каком эвристика ставила их в книгу расписания.
    """
    bounds = SortieBounds(survey_plan, survey_time, cells, sites)
    by_id = {cell.id: cell for cell in cells}
    placed = _place_boards(cells, instances, sites, serviceable, bounds)
    turnaround_s = (
        scenario.turnaround_s
        if scenario.turnaround_s is not None
        else assumptions.TURNAROUND_TIME_S
    )
    boards = [
        _Board(instance, placed[instance.key], assumptions.VEHICLE_PREP_TIME_S)
        for instance in instances
    ]
    weakest = _weakest_reach(cells, boards, sites, serviceable, bounds)
    shortest_m = min((bounds.reach_m(board.instance) for board in boards), default=0.0)
    strategy = strategy or STRATEGIES[0]
    zones = _board_zones(cells, boards, sites, bounds, strategy.sectors)
    minimum_resource = scenario.mode is OptimizationMode.MIN_RESOURCE
    remaining = {cell.id for cell in cells}
    assignments: list[SortieAssignment] = []
    stopped_by_deadline = False

    while remaining:
        ready = [board for board in boards if board.active]
        if not ready:
            break
        if minimum_resource:
            board = max(
                ready,
                key=lambda item: (bounds.rate_m2ps(item.instance), -boards.index(item)),
            )
        else:
            board = min(ready, key=lambda item: (item.free_at_s, boards.index(item)))
        # В режиме «минимум ресурса» дедлайн — ограничение: вылет собирается не
        # длиннее, чем осталось до срока (№23).
        deadline_s = scenario.deadline_s if minimum_resource else None
        cap_s = endurance_limit_s(board.instance.vehicle)
        if deadline_s is not None:
            cap_s = min(cap_s, deadline_s - board.free_at_s)
        # Самый короткий борт флота начинает вылет с ближней работы (долг №59):
        # «самая дальняя посильная» для него — подлёт на весь заряд ради минут
        # съёмки, а дальнюю работу быстрее снимут те, кто летит дальше.
        nearest_first = (
            bounds.reach_m(board.instance) <= shortest_m + assumptions.GEOMETRY_TOLERANCE_M
        )
        build = _SortieBuild(
            board,
            by_id,
            sites,
            serviceable,
            cost,
            bounds,
            weakest,
            cell_ends,
            nearest_first,
            zones.get(board.instance.key),
            strategy.fill_zone_first,
        )
        sortie, late = _plan_sortie(
            build, remaining, cap_s, placer, deadline_s, patient=minimum_resource
        )
        if sortie is None:
            board.active = False
            stopped_by_deadline |= late or cap_s < endurance_limit_s(board.instance.vehicle)
            continue
        cell_ids, duration_s, start_s = sortie
        ends_s = start_s + duration_s
        instance = board.instance
        if placer is not None:
            placer.commit(instance, board.site_id, cell_ids, start_s)
        assignments.append(
            SortieAssignment(
                sortie_id=sortie_id(instance.vehicle.id, instance.index, board.trips),
                vehicle_id=instance.vehicle.id,
                vehicle_instance=instance.index,
                takeoff_site_id=board.site_id,
                landing_site_id=board.site_id,
                cell_ids=cell_ids,
                trip_index=board.trips,
            )
        )
        remaining.difference_update(cell_ids)
        board.trips += 1
        board.free_at_s = ends_s + turnaround_s
        if instance.battery_sets is not None and board.trips >= instance.battery_sets:
            board.active = False

    unserviceable, out_of_reach, no_trips_left = _classify_leftovers(
        sorted(remaining), boards, serviceable, cost
    )
    late: list[str] = []
    if stopped_by_deadline:
        # Вылеты не кончились — кончилось время до срока.
        late, no_trips_left = no_trips_left, []
    warnings = _unassigned_warnings(unserviceable, out_of_reach, no_trips_left)
    if late:
        warnings.append(
            PlanWarning(
                code="allocate.deadline_short",
                severity=Severity.ERROR,
                stage=Stage.ALLOCATE,
                message=(
                    f"{len(late)} подобластей не успеть снять к сроку: у бортов, которые могут "
                    "их снять, до дедлайна не остаётся времени на вылет"
                ),
                context={"cell_ids": ",".join(late)},
            )
        )
    return Allocation(
        crs=crs,
        assignments=assignments,
        unassigned_cell_ids=[*unserviceable, *out_of_reach, *no_trips_left, *late],
        algorithm=ALGORITHM_NAME,
        mode=scenario.mode,
        alpha=scenario.alpha,
        seed=scenario.seed,
        warnings=warnings,
        stub=False,
    )


def _weakest_reach(
    cells: list[Cell],
    boards: list[_Board],
    sites: dict[str, MetricPoint],
    serviceable: ServiceablePredicate,
    bounds: SortieBounds,
) -> dict[str, float]:
    """Дальность самого «короткого» борта, который может снять подобласть, метры.

    Может — по нижней границе: пригоден и со своей площадки укладывается в
    заряд без обходов. Никто не может — бесконечность.
    """
    weakest: dict[str, float] = {}
    for cell in cells:
        reaches = [
            bounds.reach_m(board.instance)
            for board in boards
            if serviceable(cell.id, board.instance.vehicle.id)
            and bounds.single_floor_s(board.instance, board.site_id, cell.id)
            <= endurance_limit_s(board.instance.vehicle)
        ]
        weakest[cell.id] = min(reaches, default=math.inf)
    return weakest


@dataclass(frozen=True)
class _SortieBuild:
    """Всё, что нужно для сборки очередного вылета борта."""

    board: _Board
    cells: dict[str, Cell]
    sites: dict[str, MetricPoint]
    serviceable: ServiceablePredicate
    cost: SortieCost
    bounds: SortieBounds
    weakest: dict[str, float]
    cell_ends: CellEndsOf | None = None
    nearest_first: bool = False
    """Затравка — ближайшая подобласть, а не самая дальняя (самый короткий борт)."""
    zone: set[str] | None = None
    """Своя зона борта (`_board_zones`). None — зон нет, своё — всё."""
    fill_zone_first: bool = False
    """Добор — сначала из своей зоны (`GreedyStrategy.fill_zone_first`)."""


@dataclass(frozen=True)
class GreedyStrategy:
    """Как эвристика делит работу между бортами одной модели на одной площадке.

    Эвристика жадная, и её итог хаотичен: на данных заказчика и на демо
    лучшими оказывались разные способы делить зону площадки (журнал
    2026-09-28). Конвейер пробует все стратегии `STRATEGIES` и берёт лучший
    план по критерию режима.
    """

    name: str
    """Имя в отчёте."""
    sectors: bool
    """Зона площадки делится между её бортами одной модели на секторы по
    азимуту; иначе у всех бортов площадки одна общая зона."""
    fill_zone_first: bool
    """Добор вылета — сначала из своей зоны, потом своя работа (её не снимет
    никто короче); иначе — наоборот."""


#: Стратегии эвристики, которые пробует конвейер, — первая по умолчанию.
STRATEGIES = (
    GreedyStrategy("зоны площадок", sectors=False, fill_zone_first=False),
    GreedyStrategy("секторы бортов", sectors=True, fill_zone_first=True),
)


def _board_zones(
    cells: list[Cell],
    boards: list[_Board],
    sites: dict[str, MetricPoint],
    bounds: SortieBounds,
    sectors: bool,
) -> dict[tuple[str, int], set[str]]:
    """Зоны бортов: с какой работы каждый борт начинает (долг №60).

    Зона площадки — подобласти, для которых она ближайшая из площадок с
    бортами этой модели: иначе самолёт с южной площадки летит к подобластям у
    восточной, а восточные — к южным (`real_msk` до 2026-09-28 — около 120 км²
    чужой работы). С `sectors` зона площадки ещё и делится между её бортами
    одной модели на секторы по азимуту от площадки, поровну по времени работы:
    борта одной площадки начинают не с одной и той же самой дальней подобласти,
    а каждый со своей стороны; без `sectors` зона у них общая.

    Args:
        cells: подобласти.
        boards: борта с площадками.
        sites: площадки, метры.
        bounds: нижние границы — по ним время работы подобласти.
        sectors: делить ли зону площадки между её бортами.

    Returns:
        Подобласти своей зоны на каждый борт, по ключу (модель, экземпляр).
    """
    site_ids = list(sites)
    zones: dict[tuple[str, int], set[str]] = {}
    groups: dict[tuple[str, str], list[_Board]] = {}
    model_sites: dict[str, list[str]] = {}
    for board in boards:
        groups.setdefault((board.instance.vehicle.id, board.site_id), []).append(board)
        listed = model_sites.setdefault(board.instance.vehicle.id, [])
        if board.site_id not in listed:
            listed.append(board.site_id)
    for (model, site_id), members in groups.items():
        own = [
            cell
            for cell in cells
            if min(
                model_sites[model],
                key=lambda site: (bounds.gap_m(site, cell.id), site_ids.index(site)),
            )
            == site_id
        ]
        parts = (
            _sectors(own, members[0].instance, sites[site_id], bounds, len(members))
            if sectors
            else [list(own)] * len(members)
        )
        for board, part in zip(members, parts, strict=True):
            zones[board.instance.key] = {cell.id for cell in part}
    return zones


def _sectors(
    cells: list[Cell], instance: FleetInstance, site: MetricPoint, bounds: SortieBounds, count: int
) -> list[list[Cell]]:
    """Разрезать подобласти на `count` секторов по азимуту от площадки, поровну по времени.

    Время подобласти — галсы, растянутые на подлёт (как в `_site_load`).
    Сектора идут по кругу; начало круга — в самом широком просвете между
    соседними по азимуту подобластями, чтобы не резать скопление пополам.
    """
    if count <= 1 or not cells:
        return [list(cells)] + [[] for _ in range(max(count - 1, 0))]
    limit_s = endurance_limit_s(instance.vehicle)

    def weight(cell: Cell) -> float:
        spare_s = limit_s - bounds.overhead_s(instance, _distance(site, cell.centroid))
        survey_s = bounds.survey_s(instance, cell.id)
        return survey_s * limit_s / spare_s if spare_s > 0.0 else survey_s

    def angle(cell: Cell) -> float:
        return math.atan2(cell.centroid.y_m - site.y_m, cell.centroid.x_m - site.x_m)

    ordered = sorted(cells, key=lambda cell: (angle(cell), cell.id))
    angles = [angle(cell) for cell in ordered]
    gaps = [
        (angles[(index + 1) % len(angles)] - angles[index]) % (2.0 * math.pi)
        for index in range(len(angles))
    ]
    start = (max(range(len(gaps)), key=lambda index: (gaps[index], -index)) + 1) % len(ordered)
    ordered = ordered[start:] + ordered[:start]
    total = sum(weight(cell) for cell in ordered)
    parts: list[list[Cell]] = [[] for _ in range(count)]
    walked = 0.0
    for cell in ordered:
        share = weight(cell)
        index = min(int((walked + share / 2.0) / total * count), count - 1) if total > 0 else 0
        parts[index].append(cell)
        walked += share
    return parts


def reorder_sortie(
    instance: FleetInstance,
    site: MetricPoint,
    site_id: str,
    cell_ids: list[str],
    duration_s: float,
    cost: SortieCost,
    cell_ends: CellEndsOf | None,
) -> tuple[list[str], float] | None:
    """Переставить подобласти вылета, если так он короче (`core.allocate.order`).

    Порядок улучшается по прямым между концами змеек, а принимается, только
    если точная сборка вылета моделью полёта короче хотя бы на
    `ALLOCATE_SEARCH_TOLERANCE_S`.

    Args:
        instance: борт.
        site: площадка взлёта и посадки, метры.
        site_id: её идентификатор.
        cell_ids: подобласти по порядку облёта.
        duration_s: длительность вылета в этом порядке, секунды.
        cost: точная длительность вылета.
        cell_ends: концы змейки подобласти у модели борта; None — не переставлять.

    Returns:
        Новый порядок и длительность, секунды; None — лучше не нашлось.
    """
    if cell_ends is None or len(cell_ids) < 2:
        return None
    ends = {}
    for cell_id in cell_ids:
        item = cell_ends(cell_id, instance.vehicle.id)
        if item is None:
            return None
        ends[cell_id] = item
    order = improve_order(cell_ids, ends, site, site)
    if order == cell_ids:
        return None
    shorter = cost(instance, site_id, order)
    if shorter is None or shorter > duration_s - assumptions.ALLOCATE_SEARCH_TOLERANCE_S:
        return None
    return order, shorter


def _plan_sortie(
    build: _SortieBuild,
    remaining: set[str],
    cap_s: float,
    placer: SortiePlacer | None,
    deadline_s: float | None,
    patient: bool,
) -> tuple[tuple[list[str], float, float] | None, bool]:
    """Собрать вылет борта с оглядкой на расписание (п. 4 докстринга модуля).

    Args:
        build: борт и всё для сборки.
        remaining: непокрытые подобласти.
        cap_s: предел длительности вылета, секунды.
        placer: книга расписания; None — взлёт сразу по готовности.
        deadline_s: вылет должен закончиться к этому моменту, секунды от
            начала работ; None — срока нет.
        patient: задержку старта не лечить — ожидание на земле не тратит
            ресурс (режим «минимум ресурса»); другой вылет собирается, только
            если задержанный не успевает к сроку.

    Returns:
        Пара: (подобласти, длительность вылета в секундах, взлёт в секундах от
        начала работ) либо None — и признак «вылет был, но не успевал к сроку».
    """
    board = build.board
    ready_s = board.free_at_s
    built = _build_sortie(build, remaining, cap_s)
    if placer is None:
        return (None if built is None else (built[0], built[1], ready_s)), False

    best: tuple[list[str], float, float] | None = None
    best_rate = -math.inf
    late = False
    excluded: set[str] = set()
    crowded_tried = False
    for _attempt in range(assumptions.ALLOCATE_DELAY_RETRIES + 1):
        if built is None:
            break
        cell_ids, duration_s = built
        start_s = placer.earliest_start(board.instance, board.site_id, cell_ids, ready_s)
        if start_s is not None and deadline_s is not None and start_s + duration_s > deadline_s:
            # Сдвиг старта съел время до срока: короче вылет — ближе посадка.
            late = True
            cap_s = min(cap_s, deadline_s - start_s)
            built = _build_sortie(build, remaining - excluded, cap_s)
            continue
        if start_s is not None:
            area_m2 = sum(build.cells[cell_id].area_m2 for cell_id in cell_ids)
            rate = area_m2 / (start_s + duration_s - ready_s)
            if rate > best_rate:
                best, best_rate = (cell_ids, duration_s, start_s), rate
            if patient or start_s <= ready_s:
                break
        # Сначала — без подобластей, из-за которых сдвиг. Не помогло (затравка
        # снова ведёт в тот же район) — без всего района, где в это время
        # работают другие самолёты на той же высоте.
        blocking = placer.blocking_cells(board.instance, board.site_id, cell_ids, ready_s)
        blocking -= excluded
        if not crowded_tried and (excluded or not blocking):
            crowded_tried = True
            blocking |= placer.crowded_cells(
                board.instance, sorted(remaining - excluded), ready_s, ready_s + cap_s
            )
        if not blocking:
            break
        excluded |= blocking
        built = _build_sortie(build, remaining - excluded, cap_s)
    return best, late and best is None


def _build_sortie(
    build: _SortieBuild, remaining: set[str], cap_s: float
) -> tuple[list[str], float] | None:
    """Собрать следующий вылет борта (п. 3 докстринга модуля); None — нечего взять.

    Свои подобласти — те, что не может снять ни один борт «короче» этого
    (`weakest`), — идут первыми и в затравку, и в добор: самолёт не отбирает
    работу у коптера, пока у него есть работа, до которой коптер не долетит.
    Вылет не длиннее `cap_s` секунд (заряд или время до дедлайна).
    """
    board, cells, sites = build.board, build.cells, build.sites
    serviceable, cost, bounds, weakest = build.serviceable, build.cost, build.bounds, build.weakest
    instance = board.instance
    limit_s = cap_s
    if limit_s <= 0.0:
        return None
    site = sites[board.site_id]
    reach = bounds.reach_m(instance)
    candidates = [cell_id for cell_id in remaining if serviceable(cell_id, instance.vehicle.id)]
    own = {cell_id for cell_id in candidates if weakest[cell_id] >= reach}
    # Сначала своя зона (сектор зоны своей площадки, `_board_zones`), в ней —
    # своя работа (её не снимет никто короче), затем чужая. В своей зоне
    # дальние — раньше (короткие борта — наоборот), в чужой — ближние к своей
    # площадке: помогать соседу с края его зоны.
    zone = build.zone
    mine = set(candidates) if zone is None else {item for item in candidates if item in zone}
    direction = 1.0 if build.nearest_first else -1.0
    candidates.sort(
        key=lambda cell_id: (
            cell_id not in mine,
            cell_id not in own,
            (direction if cell_id in mine else 1.0) * _distance(site, cells[cell_id].centroid),
            cell_id,
        )
    )

    chosen: list[str] = []
    duration_s = 0.0
    for cell_id in candidates:
        if bounds.single_floor_s(instance, board.site_id, cell_id) > limit_s:
            continue
        single = cost(instance, board.site_id, [cell_id])
        if single is not None and single <= limit_s:
            chosen, duration_s = [cell_id], single
            break
    if not chosen:
        return None

    # Добор: ближайшая к последней добавленной подобласть, которая по нижней
    # границе ещё может влезть. Не влезла по точной сборке — попытка потрачена.
    # Добор кончился — подобласти переставляются (долг №58); освободилось
    # время — добор продолжается.
    pool = set(candidates) - set(chosen)
    while True:
        failures = 0
        while failures < assumptions.ALLOCATE_FILL_ATTEMPTS:
            last = cells[chosen[-1]].centroid
            fitting = [
                cell_id
                for cell_id in pool
                if duration_s + bounds.survey_s(instance, cell_id) <= limit_s
            ]
            if not fitting:
                break
            # Добор не уходит из своей зоны, пока в ней есть что взять: иначе
            # вылет растекается на зону соседа и ждёт его на разведении.
            if build.fill_zone_first:
                preferred = (
                    [item for item in fitting if item in own and item in mine]
                    or [item for item in fitting if item in mine]
                    or [item for item in fitting if item in own]
                    or fitting
                )
            else:
                preferred = (
                    [item for item in fitting if item in own and item in mine]
                    or [item for item in fitting if item in own]
                    or fitting
                )
            cell_id = min(preferred, key=lambda item: (_distance(last, cells[item].centroid), item))
            pool.discard(cell_id)
            extended = cost(instance, board.site_id, [*chosen, cell_id])
            if extended is None or extended > limit_s:
                failures += 1
                continue
            chosen.append(cell_id)
            duration_s = extended
            failures = 0
        reordered = reorder_sortie(
            instance, site, board.site_id, chosen, duration_s, cost, build.cell_ends
        )
        if reordered is None:
            return chosen, duration_s
        chosen, duration_s = reordered


def _classify_leftovers(
    leftovers: list[str],
    boards: list[_Board],
    serviceable: ServiceablePredicate,
    cost: SortieCost,
) -> tuple[list[str], list[str], list[str]]:
    """Разложить невзятые подобласти по причинам — как у baseline."""
    unserviceable: list[str] = []
    out_of_reach: list[str] = []
    no_trips_left: list[str] = []
    for cell_id in leftovers:
        capable = [board for board in boards if serviceable(cell_id, board.instance.vehicle.id)]
        if not capable:
            unserviceable.append(cell_id)
        elif all(cost(board.instance, board.site_id, [cell_id]) is None for board in capable):
            out_of_reach.append(cell_id)
        else:
            no_trips_left.append(cell_id)
    return unserviceable, out_of_reach, no_trips_left


__all__ = [
    "ALGORITHM_NAME",
    "STRATEGIES",
    "GreedyStrategy",
    "SortieBounds",
    "SortiePlacer",
    "SurveyTime",
    "build_greedy_allocation",
    "reorder_sortie",
]
