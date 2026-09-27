"""Локальный поиск по готовому распределению (E6, шаг 3).

Конструктивная эвристика (`greedy.py`) собирает вылеты по одному и к ним не
возвращается. Два места, где она теряет срок, видны только на готовом плане
(docs/current_state.md §2, долг №54):

* **хвост** — борт берёт полный вылет, когда работы осталось меньше, чем по
  вылету каждому свободному борту: он летает до конца, остальные стоят;
* **задержки в конце** — последние вылеты самолётов собираются в одном
  дальнем районе на одной высоте съёмки и ждут друг друга.

Поиск меняет план ходами и оценивает каждый **настоящим расписанием**:
вылеты ставятся в ту же книгу расписания и в том же порядке, что потом
повторит стадия расписания. Меняется хвост порядка — заново ставится только
хвост.

Ходы. Первые четыре — в режиме «минимум срока»; первые три — над
**критическим вылетом** (тот, что кончается последним):

1. **Раздать хвост.** Подобласти критического вылета — все или последние по
   порядку облёта (`ALLOCATE_SEARCH_TAIL_SHARES`) — отдаются другим бортам: в
   последний вылет борта, если помещается, или новым вылетом. Подобласть
   получает тот борт, который по оценке без задержек раньше её закончит.
2. **Переставить целиком** — новым вылетом борта, который раньше освободится.
3. **Снять задержку.** Кто уступает при разведении, решает порядок
   постановки: критический вылет ставится сразу за предыдущим вылетом своего
   борта, раньше тех, кому уступал; либо облетает подобласти в обратном
   порядке и расходится с соседями в другое время.
4. **Лишний борт — на землю.** Работа борта с наименьшей снятой площадью
   раздаётся остальным: борт на общей площадке и высоте съёмки может
   задерживать других сильнее, чем помогает.
5. **Ресурс** (оба режима). Короткий вылет растворяется в последних вылетах
   бортов: каждый лишний цикл взлёт-посадка — ресурс (№23). Срок при этом не
   растёт («минимум срока») или остаётся в дедлайне («минимум ресурса»).

Принимается лучший из вариантов хода, если он улучшает критерий режима:
сначала главный, при равенстве — второй. Принятых ходов не больше
`ALLOCATE_SEARCH_MOVES`. Случайности нет: порядок перебора задан, одно и то
же на входе — побитово один план; зерно `seed` на поиск не влияет.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from core import assumptions
from core.allocate.baseline import ServiceablePredicate, SortieCost
from core.allocate.fleet import FleetInstance, sortie_id
from core.allocate.greedy import SortieBounds, SortiePlacer
from core.allocate.objective import resource_s
from core.flight.cost import endurance_limit_s
from core.schemas import Allocation, Cell, OptimizationMode, Scenario, SortieAssignment

#: Имя алгоритма в отчётах: конструктивная эвристика плюс локальный поиск.
ALGORITHM_NAME = "constructive:earliest_free+local_search"

_TIME_EPS_S = assumptions.ALLOCATE_SEARCH_TOLERANCE_S


class ReplayPlacer(SortiePlacer, Protocol):
    """Книга расписания с откатом: поиск ставит заново только изменённый хвост."""

    def rewind(self, count: int) -> None:
        """Оставить только первые `count` поставленных вылетов."""
        ...


@dataclass(frozen=True)
class _Trip:
    """Вылет плана: борт, площадка (взлёт и посадка), подобласти по порядку облёта."""

    instance: FleetInstance
    site_id: str
    cell_ids: tuple[str, ...]
    duration_s: float

    @property
    def key(self) -> tuple[str, int, str, tuple[str, ...]]:
        return (*self.instance.key, self.site_id, self.cell_ids)


@dataclass(frozen=True)
class _Outcome:
    """Оценка плана расписанием."""

    starts: list[float]
    makespan_s: float
    resource_s: float

    def end(self, trips: list[_Trip], index: int) -> float:
        return self.starts[index] + trips[index].duration_s


class _Replay:
    """Постановка вылетов в книгу по порядку; изменённый хвост — заново."""

    def __init__(self, placer: ReplayPlacer, turnaround_s: float) -> None:
        self._placer = placer
        self._turnaround_s = turnaround_s
        self._trips: list[_Trip] = []
        self._starts: list[float] = []
        placer.rewind(0)

    def evaluate(self, trips: list[_Trip]) -> _Outcome | None:
        """Поставить вылеты по порядку. None — какому-то вылету нет окна."""
        same = 0
        limit = min(len(trips), len(self._trips))
        while same < limit and trips[same].key == self._trips[same].key:
            same += 1
        self._placer.rewind(same)
        del self._trips[same:]
        del self._starts[same:]
        ready: dict[tuple[str, int], float] = {}
        for trip, start_s in zip(self._trips, self._starts, strict=True):
            ready[trip.instance.key] = start_s + trip.duration_s + self._turnaround_s
        for trip in trips[same:]:
            ready_s = ready.get(trip.instance.key, assumptions.VEHICLE_PREP_TIME_S)
            cells = list(trip.cell_ids)
            start_s = self._placer.earliest_start(trip.instance, trip.site_id, cells, ready_s)
            if start_s is None:
                return None
            self._placer.commit(trip.instance, trip.site_id, cells, start_s)
            self._trips.append(trip)
            self._starts.append(start_s)
            ready[trip.instance.key] = start_s + trip.duration_s + self._turnaround_s
        makespan = max(
            (start + trip.duration_s for trip, start in zip(trips, self._starts, strict=True)),
            default=0.0,
        )
        flight = sum(trip.duration_s for trip in trips)
        return _Outcome(list(self._starts), makespan, resource_s(flight, len(trips)))


class _Search:
    """Ходы локального поиска над списком вылетов в порядке постановки."""

    def __init__(
        self,
        scenario: Scenario,
        instances: list[FleetInstance],
        cells: dict[str, Cell],
        serviceable: ServiceablePredicate,
        cost: SortieCost,
        bounds: SortieBounds,
        replay: _Replay,
    ) -> None:
        self.scenario = scenario
        self.instances = instances
        self.cells = cells
        self.serviceable = serviceable
        self.cost = cost
        self.bounds = bounds
        self.replay = replay
        self.turnaround_s = (
            scenario.turnaround_s
            if scenario.turnaround_s is not None
            else assumptions.TURNAROUND_TIME_S
        )
        self.minimum_resource = scenario.mode is OptimizationMode.MIN_RESOURCE

    # --- критерий ---------------------------------------------------------

    def feasible(self, outcome: _Outcome) -> bool:
        """Дедлайн — ограничение режима «минимум ресурса» (№23)."""
        deadline = self.scenario.deadline_s
        return not (
            self.minimum_resource and deadline is not None and outcome.makespan_s > deadline
        )

    def _key(self, outcome: _Outcome) -> tuple[float, float]:
        if self.minimum_resource:
            return outcome.resource_s, outcome.makespan_s
        return outcome.makespan_s, outcome.resource_s

    def better(self, new: _Outcome, old: _Outcome) -> bool:
        """Лучше ли `new` по критерию режима: главный, при равенстве — второй."""
        if not self.feasible(new):
            return False
        (new_main, new_second), (old_main, old_second) = self._key(new), self._key(old)
        if new_main < old_main - _TIME_EPS_S:
            return True
        return abs(new_main - old_main) <= _TIME_EPS_S and new_second < old_second - _TIME_EPS_S

    # --- раздача подобластей ----------------------------------------------

    def _site_of(self, trips: list[_Trip], instance: FleetInstance) -> str | None:
        for trip in trips:
            if trip.instance.key == instance.key:
                return trip.site_id
        return instance.site_id

    def _insert_near(self, cell_ids: tuple[str, ...], cell_id: str) -> tuple[str, ...]:
        """Вставить подобласть в порядок облёта сразу за ближайшей к ней."""
        point = self.cells[cell_id].centroid
        nearest = min(
            range(len(cell_ids)),
            key=lambda index: (
                math.hypot(
                    self.cells[cell_ids[index]].centroid.x_m - point.x_m,
                    self.cells[cell_ids[index]].centroid.y_m - point.y_m,
                ),
                index,
            ),
        )
        return (*cell_ids[: nearest + 1], cell_id, *cell_ids[nearest + 1 :])

    def distribute(
        self,
        moving: list[str],
        trips: list[_Trip],
        outcome: _Outcome,
        source: tuple[str, int] | None,
        new_trips: bool,
        finish_by_s: float,
    ) -> list[_Trip] | None:
        """Раздать подобласти другим бортам; None — какую-то некому взять.

        Подобласть получает тот борт, который по оценке без задержек раньше
        её закончит: дописать её в последний вылет борта (за ближайшей к ней
        подобластью) или, если `new_trips`, — новым вылетом после последнего.
        Закончить надо раньше `finish_by_s` секунд от начала работ.

        Args:
            moving: подобласти, по порядку.
            trips: план без них.
            outcome: оценка плана до хода — старты вылетов.
            source: борт, у которого их забирают; ему не отдаются.
            new_trips: можно ли заводить новые вылеты.
            finish_by_s: предел окончания, секунды.
        """
        trips = list(trips)
        starts = list(outcome.starts[: len(trips)])
        last: dict[tuple[str, int], int] = {}
        for index, trip in enumerate(trips):
            last[trip.instance.key] = index
        counts: dict[tuple[str, int], int] = {}
        for trip in trips:
            counts[trip.instance.key] = counts.get(trip.instance.key, 0) + 1

        for cell_id in moving:
            options: list[tuple[float, int, FleetInstance, int | None, str]] = []
            for order, instance in enumerate(self.instances):
                if instance.key == source or not self.serviceable(cell_id, instance.vehicle.id):
                    continue
                site_id = self._site_of(trips, instance)
                if site_id is None:
                    continue
                survey_s = self.bounds.survey_s(instance, cell_id)
                index = last.get(instance.key)
                if index is not None:
                    floor = starts[index] + trips[index].duration_s + survey_s
                    options.append((floor, order, instance, index, site_id))
                trips_left = (
                    instance.battery_sets is None
                    or counts.get(instance.key, 0) < instance.battery_sets
                )
                if new_trips and trips_left:
                    free_s = (
                        starts[index] + trips[index].duration_s + self.turnaround_s
                        if index is not None
                        else assumptions.VEHICLE_PREP_TIME_S
                    )
                    floor = free_s + self.bounds.single_floor_s(instance, site_id, cell_id)
                    options.append((floor, order, instance, None, site_id))
            options.sort(key=lambda item: (item[0], item[1], item[3] is None))

            best: tuple[float, FleetInstance, int | None, str, tuple[str, ...], float] | None = None
            for floor, _order, instance, index, site_id in options:
                bound = finish_by_s if best is None else min(best[0], finish_by_s)
                if floor >= bound:
                    break
                limit_s = endurance_limit_s(instance.vehicle)
                if index is not None:
                    host = trips[index]
                    if host.duration_s + self.bounds.survey_s(instance, cell_id) > limit_s:
                        continue
                    cell_ids = self._insert_near(host.cell_ids, cell_id)
                    duration = self.cost(instance, site_id, list(cell_ids))
                    if duration is None:
                        continue
                    end_s = starts[index] + duration
                else:
                    cell_ids = (cell_id,)
                    duration = self.cost(instance, site_id, [cell_id])
                    if duration is None:
                        continue
                    previous = last.get(instance.key)
                    free_s = (
                        starts[previous] + trips[previous].duration_s + self.turnaround_s
                        if previous is not None
                        else assumptions.VEHICLE_PREP_TIME_S
                    )
                    end_s = free_s + duration
                if end_s < bound:
                    best = (end_s, instance, index, site_id, cell_ids, duration)
            if best is None:
                return None
            end_s, instance, index, site_id, cell_ids, duration = best
            trip = _Trip(instance, site_id, cell_ids, duration)
            if index is not None:
                trips[index] = trip
            else:
                trips.append(trip)
                starts.append(end_s - duration)
                last[instance.key] = len(trips) - 1
                counts[instance.key] = counts.get(instance.key, 0) + 1
        return trips

    # --- ходы -------------------------------------------------------------

    def critical(self, trips: list[_Trip], outcome: _Outcome) -> int:
        """Номер вылета, который кончается последним."""
        return max(range(len(trips)), key=lambda index: (outcome.end(trips, index), index))

    def tail_moves(self, trips: list[_Trip], outcome: _Outcome) -> list[list[_Trip]]:
        """Варианты ходов 1–3: разгрузить критический вылет или снять его задержку."""
        index = self.critical(trips, outcome)
        critical = trips[index]
        finish_by = outcome.makespan_s - _TIME_EPS_S
        variants: list[list[_Trip]] = self.priority_moves(trips, index)
        counts: set[int] = set()
        for share in assumptions.ALLOCATE_SEARCH_TAIL_SHARES:
            count = max(1, math.ceil(len(critical.cell_ids) * share))
            if count in counts:
                continue
            counts.add(count)
            kept = critical.cell_ids[: len(critical.cell_ids) - count]
            moving = list(critical.cell_ids[len(critical.cell_ids) - count :])
            rest = [trip for position, trip in enumerate(trips) if position != index]
            base = outcome
            if kept:
                duration = self.cost(critical.instance, critical.site_id, list(kept))
                if duration is None:
                    continue
                rest = list(trips)
                rest[index] = _Trip(critical.instance, critical.site_id, kept, duration)
            else:
                # Без критического вылета старты последующих не известны — для
                # оценки хватит прежних: раздача сверяется с ними, а решает
                # настоящее расписание.
                base = _Outcome(
                    [start for position, start in enumerate(outcome.starts) if position != index],
                    outcome.makespan_s,
                    outcome.resource_s,
                )
            moved = self.distribute(
                moving, rest, base, critical.instance.key, new_trips=True, finish_by_s=finish_by
            )
            if moved is not None:
                variants.append(moved)

        # Ход 2: весь вылет — новым вылетом борта, который раньше освободится.
        rest = [trip for position, trip in enumerate(trips) if position != index]
        ends: dict[tuple[str, int], float] = {}
        counts_by_board: dict[tuple[str, int], int] = {}
        for position, trip in enumerate(trips):
            if position == index:
                continue
            ends[trip.instance.key] = outcome.end(trips, position)
            counts_by_board[trip.instance.key] = counts_by_board.get(trip.instance.key, 0) + 1
        for instance in sorted(
            self.instances,
            key=lambda item: (ends.get(item.key, 0.0), self.instances.index(item)),
        ):
            if instance.key == critical.instance.key:
                continue
            if instance.battery_sets is not None and (
                counts_by_board.get(instance.key, 0) >= instance.battery_sets
            ):
                continue
            if not all(self.serviceable(cell, instance.vehicle.id) for cell in critical.cell_ids):
                continue
            site_id = self._site_of(trips, instance)
            if site_id is None:
                continue
            free_s = (
                ends[instance.key] + self.turnaround_s
                if instance.key in ends
                else assumptions.VEHICLE_PREP_TIME_S
            )
            if free_s >= finish_by:
                break
            first, *others = critical.cell_ids
            floor_s = self.bounds.single_floor_s(instance, site_id, first) + sum(
                self.bounds.survey_s(instance, cell_id) for cell_id in others
            )
            if floor_s > endurance_limit_s(instance.vehicle) or free_s + floor_s >= finish_by:
                continue
            duration = self.cost(instance, site_id, list(critical.cell_ids))
            if duration is None or free_s + duration >= finish_by:
                continue
            variants.append([*rest, _Trip(instance, site_id, critical.cell_ids, duration)])
            break
        return variants

    def priority_moves(self, trips: list[_Trip], index: int) -> list[list[_Trip]]:
        """Ход 3: снять с критического вылета задержку разведения.

        Кто уступает при разведении, решает порядок постановки: вылет,
        поставленный раньше, взлетает по готовности, остальные его обходят по
        времени. Варианты: поставить критический вылет сразу за предыдущим
        вылетом его борта — раньше всех, кому он уступал; облететь его
        подобласти в обратном порядке — с соседями он разминётся в другое время.
        """
        critical = trips[index]
        variants: list[list[_Trip]] = []
        previous = max(
            (
                position
                for position in range(index)
                if trips[position].instance.key == critical.instance.key
            ),
            default=-1,
        )
        if previous + 1 < index:
            promoted = [trip for position, trip in enumerate(trips) if position != index]
            promoted.insert(previous + 1, critical)
            variants.append(promoted)
        if len(critical.cell_ids) > 1:
            reverse = critical.cell_ids[::-1]
            duration = self.cost(critical.instance, critical.site_id, list(reverse))
            if duration is not None:
                reversed_trips = list(trips)
                reversed_trips[index] = _Trip(
                    critical.instance, critical.site_id, reverse, duration
                )
                variants.append(reversed_trips)
        return variants

    def drop_moves(self, trips: list[_Trip], outcome: _Outcome) -> Iterator[list[_Trip]]:
        """Ход 4: лишний борт — на землю.

        Больше бортов — не всегда быстрее: борт на общей площадке и высоте
        съёмки заставляет других ждать разведения (долг №54). Работа борта
        раздаётся остальным; кандидаты — борта с наименьшей снятой площадью,
        не больше `ALLOCATE_SEARCH_DROP_CANDIDATES`. Варианты — по одному.
        """
        finish_by = outcome.makespan_s + _TIME_EPS_S
        area: dict[tuple[str, int], float] = {}
        for trip in trips:
            area[trip.instance.key] = area.get(trip.instance.key, 0.0) + sum(
                self.cells[cell_id].area_m2 for cell_id in trip.cell_ids
            )
        order = [instance.key for instance in self.instances]
        boards = sorted(area, key=lambda key: (area[key], order.index(key)))
        for key in boards[: assumptions.ALLOCATE_SEARCH_DROP_CANDIDATES]:
            moving = [
                cell_id for trip in trips if trip.instance.key == key for cell_id in trip.cell_ids
            ]
            kept = [position for position, trip in enumerate(trips) if trip.instance.key != key]
            base = _Outcome(
                [outcome.starts[position] for position in kept],
                outcome.makespan_s,
                outcome.resource_s,
            )
            moved = self.distribute(
                moving,
                [trips[position] for position in kept],
                base,
                key,
                new_trips=True,
                finish_by_s=finish_by,
            )
            if moved is not None:
                yield moved

    def merge_moves(self, trips: list[_Trip], outcome: _Outcome) -> Iterator[list[_Trip]]:
        """Ход 5: короткие вылеты по очереди — в последние вылеты бортов.

        Варианты выдаются по одному: поиск берёт первый, который улучшил план.
        """
        finish_by = (
            outcome.makespan_s + _TIME_EPS_S
            if not self.minimum_resource
            else (self.scenario.deadline_s if self.scenario.deadline_s is not None else math.inf)
        )
        order = sorted(range(len(trips)), key=lambda index: (trips[index].duration_s, index))
        for index in order[: assumptions.ALLOCATE_SEARCH_MERGE_CANDIDATES]:
            victim = trips[index]
            rest = [trip for position, trip in enumerate(trips) if position != index]
            base = _Outcome(
                [start for position, start in enumerate(outcome.starts) if position != index],
                outcome.makespan_s,
                outcome.resource_s,
            )
            # Свой борт тоже годится: его последний вылет (после удаления этого)
            # примет подобласти без лишнего взлёта.
            moved = self.distribute(
                list(victim.cell_ids), rest, base, None, new_trips=False, finish_by_s=finish_by
            )
            if moved is not None:
                yield moved


def improve_allocation(
    allocation: Allocation,
    scenario: Scenario,
    instances: list[FleetInstance],
    cells: list[Cell],
    serviceable: ServiceablePredicate,
    cost: SortieCost,
    bounds: SortieBounds,
    placer: ReplayPlacer,
) -> Allocation:
    """Улучшить распределение ходами, оценивая каждый расписанием (докстринг модуля).

    Args:
        allocation: распределение эвристики; вылеты — в порядке постановки в
            расписание.
        scenario: сценарий (режим, дедлайн, межвылетная подготовка).
        instances: физические борта.
        cells: подобласти.
        serviceable: пригодность пары «подобласть × модель борта».
        cost: длительность вылета моделью полёта, секунды; None — не помещается.
        bounds: нижние границы времени вылета.
        placer: книга расписания с откатом.

    Returns:
        Распределение не хуже исходного по критерию режима. Вылеты — в
        порядке постановки в расписание; номера вылетов бортов — по этому
        порядку. Распределение с невзятыми подобластями не меняется: план
        всё равно невыполним.
    """
    if allocation.unassigned_cell_ids or not allocation.assignments:
        return allocation
    by_key = {instance.key: instance for instance in instances}
    trips: list[_Trip] = []
    for assignment in allocation.assignments:
        instance = by_key[(assignment.vehicle_id, assignment.vehicle_instance)]
        duration = cost(instance, assignment.takeoff_site_id, assignment.cell_ids)
        if duration is None:
            return allocation
        trips.append(
            _Trip(instance, assignment.takeoff_site_id, tuple(assignment.cell_ids), duration)
        )

    turnaround_s = (
        scenario.turnaround_s
        if scenario.turnaround_s is not None
        else assumptions.TURNAROUND_TIME_S
    )
    replay = _Replay(placer, turnaround_s)
    search = _Search(
        scenario, instances, {cell.id: cell for cell in cells}, serviceable, cost, bounds, replay
    )
    current = replay.evaluate(trips)
    if current is None:
        return allocation

    moves = 0
    # Хвост, для которого ходы 1–3 ничего не дали: пока он тот же, их не повторять.
    stuck: tuple[tuple[str, int, str, tuple[str, ...]], float] | None = None
    while moves < assumptions.ALLOCATE_SEARCH_MOVES:
        chosen: tuple[list[_Trip], _Outcome] | None = None
        if not search.minimum_resource:
            critical = trips[search.critical(trips, current)]
            tail = (critical.key, current.makespan_s)
            if tail != stuck:
                for variant in search.tail_moves(trips, current):
                    outcome = replay.evaluate(variant)
                    if outcome is not None and search.better(
                        outcome, chosen[1] if chosen else current
                    ):
                        chosen = (variant, outcome)
                if chosen is None:
                    for variant in search.drop_moves(trips, current):
                        outcome = replay.evaluate(variant)
                        if outcome is not None and search.better(outcome, current):
                            chosen = (variant, outcome)
                            break
                if chosen is None:
                    stuck = tail
        if chosen is None:
            for variant in search.merge_moves(trips, current):
                outcome = replay.evaluate(variant)
                if outcome is not None and search.better(outcome, current):
                    chosen = (variant, outcome)
                    break
        if chosen is None:
            break
        trips, current = chosen
        moves += 1

    return allocation.model_copy(
        update={"assignments": _assignments(trips), "algorithm": ALGORITHM_NAME}
    )


def _assignments(trips: list[_Trip]) -> list[SortieAssignment]:
    """Назначения в порядке постановки; номера вылетов борта — по этому порядку."""
    counts: dict[tuple[str, int], int] = {}
    result: list[SortieAssignment] = []
    for trip in trips:
        instance = trip.instance
        trip_index = counts.get(instance.key, 0)
        counts[instance.key] = trip_index + 1
        result.append(
            SortieAssignment(
                sortie_id=sortie_id(instance.vehicle.id, instance.index, trip_index),
                vehicle_id=instance.vehicle.id,
                vehicle_instance=instance.index,
                takeoff_site_id=trip.site_id,
                landing_site_id=trip.site_id,
                cell_ids=list(trip.cell_ids),
                trip_index=trip_index,
            )
        )
    return result


__all__ = ["ALGORITHM_NAME", "ReplayPlacer", "improve_allocation"]
