"""Baseline: равномерное деление области по бортам.

ИНВАРИАНТ 8. Этот алгоритм НИКОГДА не удаляется. Он прост, всегда даёт
результат и служит точкой сравнения, которой доказывается эффективность
настоящего оптимизатора (E12). Если он перестал запускаться — сломан проект,
а не baseline.

Что он делает:

1. Берёт подобласти в порядке змейки по рядам сетки и режет их на подряд
   идущие куски почти равной площади — по одному на борт.
2. Сопоставляет куски бортам по близости площадки: борт, приписанный к
   площадке сценарием, получает кусок рядом с ней; борту без площадки
   назначается ближайшая к его куску. Взлёт и посадка — там же, во всех
   вылетах (борт взлетает там, где сел).
3. Внутри куска набирает вылеты по вместимости.
4. Подобласти, которые хозяину куска непригодны или не влезли, во втором
   проходе отдаёт наименее загруженному из бортов, способных их снять,
   упаковывая подряд идущие в один вылет.

Чего он НЕ делает и не должен: не учитывает разную производительность
бортов при делении (самолёт и коптер получают равные площади), не переносит
подобласти ради критерия, не смотрит на режим оптимизации.

Невзятые подобласти не «теряются молча»: они перечисляются с причиной, и
конвейер объявляет задание невыполнимым (инвариант 10).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from core.allocate.fleet import FleetInstance
from core.schemas import (
    Allocation,
    Cell,
    CrsInfo,
    MetricPoint,
    PlanWarning,
    Scenario,
    Severity,
    SortieAssignment,
    Stage,
)

#: Имя алгоритма в отчётах и в сравнении с оптимизатором.
ALGORITHM_NAME = "baseline:uniform_split"

#: Длительность вылета борта с площадки над набором подобластей, секунды,
#: либо None, если вылет не укладывается в заряд. Реализацию даёт модель
#: полёта (E4) — распределение не знает, как считается время.
SortieCost = Callable[[FleetInstance, str, list[str]], float | None]

#: Может ли модель борта снять подобласть без нарушения жёстких правил (E3).
ServiceablePredicate = Callable[[str, str], bool]


def _split_by_area(cells: list[Cell], parts: int) -> list[list[Cell]]:
    """Разрезать список на `parts` подряд идущих кусков почти равной площади."""
    if parts <= 0:
        return []
    total = sum(cell.area_m2 for cell in cells)
    target = total / parts
    chunks: list[list[Cell]] = [[] for _ in range(parts)]
    accumulated = 0.0
    for cell in cells:
        index = min(int(accumulated / target), parts - 1) if target > 0 else 0
        chunks[index].append(cell)
        accumulated += cell.area_m2
    return chunks


def _centroid(chunk: list[Cell]) -> tuple[float, float] | None:
    """Центр тяжести куска подобластей, метры."""
    area = sum(cell.area_m2 for cell in chunk)
    if not chunk or area <= 0.0:
        return None
    x = sum(cell.centroid.x_m * cell.area_m2 for cell in chunk) / area
    y = sum(cell.centroid.y_m * cell.area_m2 for cell in chunk) / area
    return x, y


def _match_chunks(
    chunks: list[list[Cell]], instances: list[FleetInstance], sites: dict[str, MetricPoint]
) -> list[tuple[list[Cell], FleetInstance, str]]:
    """Сопоставить куски бортам: ближайшие пары «кусок — площадка борта» первыми.

    Борт с площадкой из сценария получает кусок рядом с ней; борт без площадки
    — кусок, а площадкой ему становится ближайшая к куску. Жадно по
    возрастанию расстояния, при равенстве — по порядку: результат
    детерминирован.
    """
    pairs: list[tuple[float, int, int, str]] = []
    for chunk_index, chunk in enumerate(chunks):
        center = _centroid(chunk)
        for instance_index, instance in enumerate(instances):
            candidates = [instance.site_id] if instance.site_id else list(sites)
            if center is None:
                pairs.append((0.0, chunk_index, instance_index, candidates[0]))
                continue
            site_id = min(
                candidates,
                key=lambda item: math.hypot(
                    sites[item].x_m - center[0], sites[item].y_m - center[1]
                ),
            )
            distance = math.hypot(sites[site_id].x_m - center[0], sites[site_id].y_m - center[1])
            pairs.append((distance, chunk_index, instance_index, site_id))
    pairs.sort()

    used_chunks: set[int] = set()
    used_instances: set[int] = set()
    matched: dict[int, tuple[list[Cell], FleetInstance, str]] = {}
    for _distance, chunk_index, instance_index, site_id in pairs:
        if chunk_index in used_chunks or instance_index in used_instances:
            continue
        used_chunks.add(chunk_index)
        used_instances.add(instance_index)
        matched[instance_index] = (chunks[chunk_index], instances[instance_index], site_id)
    return [matched[index] for index in range(len(instances))]


class _Packer:
    """Набор вылетов с учётом предела вылетов и загрузки каждого борта."""

    def __init__(self, cost: SortieCost, site_of: dict[tuple[str, int], str]) -> None:
        self.cost = cost
        self.site_of = site_of
        self.assignments: list[SortieAssignment] = []
        self.trips: dict[tuple[str, int], int] = {}
        self.load_s: dict[tuple[str, int], float] = {}

    def duration(self, instance: FleetInstance, cell_ids: list[str]) -> float | None:
        """Длительность вылета борта над подобластями либо None."""
        return self.cost(instance, self.site_of[instance.key], cell_ids)

    def can_fly(self, instance: FleetInstance) -> bool:
        """Остались ли у борта вылеты."""
        used = self.trips.get(instance.key, 0)
        return instance.battery_sets is None or used < instance.battery_sets

    def close(self, instance: FleetInstance, cell_ids: list[str], duration_s: float) -> bool:
        """Оформить вылет. False — у борта кончились вылеты."""
        if not cell_ids:
            return True
        if not self.can_fly(instance):
            return False
        trip = self.trips.get(instance.key, 0)
        self.trips[instance.key] = trip + 1
        self.load_s[instance.key] = self.load_s.get(instance.key, 0.0) + duration_s
        site_id = self.site_of[instance.key]
        self.assignments.append(
            SortieAssignment(
                sortie_id=f"{instance.vehicle.id}-{instance.index}-t{trip}",
                vehicle_id=instance.vehicle.id,
                vehicle_instance=instance.index,
                takeoff_site_id=site_id,
                landing_site_id=site_id,
                cell_ids=list(cell_ids),
                trip_index=trip,
            )
        )
        return True


def build_baseline_allocation(
    scenario: Scenario,
    crs: CrsInfo,
    cells: list[Cell],
    instances: list[FleetInstance],
    sites: dict[str, MetricPoint],
    serviceable: ServiceablePredicate,
    cost: SortieCost,
) -> Allocation:
    """Распределить подобласти по бортам равномерным делением.

    Детерминированность: случайности нет вовсе, `scenario.seed` на результат
    не влияет и записывается только для сопоставимости с оптимизатором (E6).

    Args:
        scenario: сценарий (режим, α, seed).
        crs: рабочая проекция.
        cells: подобласти в порядке змейки.
        instances: физические борта.
        sites: штатные площадки в метрах.
        serviceable: пригодность пары «подобласть × модель борта».
        cost: длительность вылета либо None, если он не укладывается в заряд.

    Returns:
        Распределение с невзятыми подобластями и причинами.
    """
    matched = _match_chunks(_split_by_area(cells, len(instances)), instances, sites)
    site_of = {instance.key: site_id for _chunk, instance, site_id in matched}
    packer = _Packer(cost, site_of)
    leftovers: list[str] = []

    for chunk, instance, _site_id in matched:
        current: list[str] = []
        current_s = 0.0
        for cell in chunk:
            if not serviceable(cell.id, instance.vehicle.id):
                leftovers.append(cell.id)
                continue
            extended = packer.duration(instance, [*current, cell.id])
            if extended is not None:
                current, current_s = [*current, cell.id], extended
                continue
            if not packer.close(instance, current, current_s):
                leftovers.extend(current)
            single = packer.duration(instance, [cell.id])
            if single is not None:
                current, current_s = [cell.id], single
            else:
                current, current_s = [], 0.0
                leftovers.append(cell.id)
        if not packer.close(instance, current, current_s):
            leftovers.extend(current)

    unserviceable: list[str] = []
    out_of_reach: list[str] = []
    no_trips_left: list[str] = []
    pending = list(leftovers)
    while pending:
        cell_id = pending.pop(0)
        capable = [item for item in instances if serviceable(cell_id, item.vehicle.id)]
        if not capable:
            unserviceable.append(cell_id)
            continue
        fitting = [(item, packer.duration(item, [cell_id])) for item in capable]
        fitting = [(item, seconds) for item, seconds in fitting if seconds is not None]
        if not fitting:
            out_of_reach.append(cell_id)
            continue
        flying = [(item, seconds) for item, seconds in fitting if packer.can_fly(item)]
        if not flying:
            no_trips_left.append(cell_id)
            continue
        chosen, group_s = min(flying, key=lambda pair: packer.load_s.get(pair[0].key, 0.0))
        group = [cell_id]
        while pending and serviceable(pending[0], chosen.vehicle.id):
            extended = packer.duration(chosen, [*group, pending[0]])
            if extended is None:
                break
            group.append(pending.pop(0))
            group_s = extended
        packer.close(chosen, group, group_s)

    warnings = _unassigned_warnings(unserviceable, out_of_reach, no_trips_left)
    return Allocation(
        crs=crs,
        assignments=packer.assignments,
        unassigned_cell_ids=[*unserviceable, *out_of_reach, *no_trips_left],
        algorithm=ALGORITHM_NAME,
        mode=scenario.mode,
        alpha=scenario.alpha,
        seed=scenario.seed,
        warnings=warnings,
        stub=False,
    )


def _unassigned_warnings(
    unserviceable: list[str], out_of_reach: list[str], no_trips_left: list[str]
) -> list[PlanWarning]:
    """Причины, по которым подобласти остались без вылета."""
    warnings: list[PlanWarning] = []
    if unserviceable:
        warnings.append(
            PlanWarning(
                code="allocate.cells_unserviceable",
                severity=Severity.ERROR,
                stage=Stage.ALLOCATE,
                message=(
                    f"{len(unserviceable)} подобластей не может снять ни один борт флота: "
                    "у самолётов зона манёвра задевает запретную зону или препятствие, а "
                    "мультироторов, которым она не нужна, нет"
                ),
                context={"cell_ids": ",".join(unserviceable)},
            )
        )
    if out_of_reach:
        warnings.append(
            PlanWarning(
                code="allocate.cells_out_of_reach",
                severity=Severity.ERROR,
                stage=Stage.ALLOCATE,
                message=(
                    f"{len(out_of_reach)} подобластей не укладываются ни в один вылет: "
                    "подлёт, съёмка подобласти и возврат дольше доступных 80% заряда "
                    "у всех бортов, которые могут её снять"
                ),
                context={"cell_ids": ",".join(out_of_reach)},
            )
        )
    if no_trips_left:
        warnings.append(
            PlanWarning(
                code="allocate.no_trips_left",
                severity=Severity.ERROR,
                stage=Stage.ALLOCATE,
                message=(
                    f"{len(no_trips_left)} подобластей не хватило вылетов: у бортов, "
                    "которые могут их снять, исчерпан заданный предел вылетов"
                ),
                context={"cell_ids": ",".join(no_trips_left)},
            )
        )
    return warnings


__all__ = [
    "ALGORITHM_NAME",
    "ServiceablePredicate",
    "SortieCost",
    "build_baseline_allocation",
]
