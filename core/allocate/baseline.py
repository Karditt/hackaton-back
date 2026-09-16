"""Baseline: равномерное деление галсов по бортам.

ИНВАРИАНТ 8. Этот алгоритм НИКОГДА не удаляется. Он прост, всегда даёт
результат и служит точкой сравнения, которой доказывается эффективность
настоящего оптимизатора (E12). Если он перестал запускаться — сломан проект,
а не baseline.

Что он делает: режет список галсов на равные подряд идущие куски по числу
экземпляров бортов, затем внутри куска нарезает вылеты по вместимости борта.
Чего он НЕ делает и не должен: не учитывает ветер при выборе порядка, не
переносит галсы между бортами, не смешивает типы бортов осмысленно.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from core.schemas import (
    Allocation,
    CoverageSet,
    FleetUnit,
    PlanWarning,
    Scenario,
    Severity,
    SiteKind,
    SortieAssignment,
    Stage,
    Strip,
    Vehicle,
)

#: Имя алгоритма в отчётах и в сравнении с оптимизатором.
ALGORITHM_NAME = "baseline:uniform_split"

#: Подходит ли набор галсов одному вылету борта с заданной площадки.
#: Реализацию даёт модель полёта (E4) — распределение не должно знать, как
#: считается время. Площадка входит в сигнатуру, потому что от неё зависит
#: длина подлёта, а значит и вместимость вылета.
FitsPredicate = Callable[[Vehicle, str, list[Strip]], bool]


class _Instance:
    """Один физический борт: строка флота, экземпляр, площадка."""

    __slots__ = ("vehicle", "index", "site_id", "battery_sets")

    def __init__(self, vehicle: Vehicle, index: int, site_id: str, battery_sets: int) -> None:
        self.vehicle = vehicle
        self.index = index
        self.site_id = site_id
        self.battery_sets = battery_sets


def _expand_fleet(
    scenario: Scenario, vehicles: dict[str, Vehicle], usable: Iterable[str]
) -> list[_Instance]:
    """Развернуть состав флота в список отдельных бортов.

    Args:
        scenario: сценарий.
        vehicles: каталог бортов.
        usable: идентификаторы бортов, пригодных для данного типа съёмки.

    Returns:
        Список экземпляров бортов с приписанными площадками.
    """
    allowed = set(usable)
    sites = scenario.primary_sites() or scenario.sites
    instances: list[_Instance] = []
    cursor = 0

    for unit in scenario.fleet:
        if unit.vehicle_id not in allowed:
            continue
        for index in range(unit.count):
            site_id = unit.site_id or sites[cursor % len(sites)].id
            cursor += 1
            instances.append(
                _Instance(vehicles[unit.vehicle_id], index, site_id, unit.battery_sets)
            )
    return instances


def _split_evenly(strips: list[Strip], parts: int) -> list[list[Strip]]:
    """Разрезать список на `parts` подряд идущих кусков почти равной длины."""
    if parts <= 0:
        return []
    size, remainder = divmod(len(strips), parts)
    chunks: list[list[Strip]] = []
    start = 0
    for index in range(parts):
        length = size + (1 if index < remainder else 0)
        chunks.append(strips[start : start + length])
        start += length
    return chunks


def build_baseline_allocation(
    scenario: Scenario,
    coverage: CoverageSet,
    vehicles: dict[str, Vehicle],
    usable_vehicle_ids: Iterable[str],
    fits: FitsPredicate,
) -> Allocation:
    """Распределить галсы по бортам равномерным делением.

    Многовылетность поддерживается: если кусок не влезает в один вылет, он
    режется на несколько по предикату `fits`. Число вылетов борта ограничено
    числом комплектов АКБ (`FleetUnit.battery_sets`).

    Детерминированность: алгоритм не использует случайность вовсе, поэтому
    `scenario.seed` на результат не влияет — он записывается в `Allocation`
    только для сопоставимости с будущими рандомизированными алгоритмами (E6).

    Args:
        scenario: сценарий (флот, площадки, α, seed).
        coverage: набор галсов в метрах.
        vehicles: каталог бортов по идентификаторам.
        usable_vehicle_ids: борта, прошедшие отбор по совместимости (E2).
        fits: предикат «этот набор галсов помещается в один вылет борта».

    Returns:
        Распределение: список назначений плюс предупреждения стадии.
    """
    warnings: list[PlanWarning] = []
    instances = _expand_fleet(scenario, vehicles, usable_vehicle_ids)

    if not instances:
        warnings.append(
            PlanWarning(
                code="allocate.no_vehicles",
                severity=Severity.ERROR,
                stage=Stage.ALLOCATE,
                message="После отбора по совместимости во флоте не осталось ни одного борта",
            )
        )
        return Allocation(
            crs=coverage.crs,
            assignments=[],
            algorithm=ALGORITHM_NAME,
            alpha=scenario.alpha,
            seed=scenario.seed,
            warnings=warnings,
            stub=False,
        )

    reserve_only = [site.id for site in scenario.sites if site.kind is SiteKind.RESERVE]
    if reserve_only and not scenario.primary_sites():
        warnings.append(
            PlanWarning(
                code="allocate.reserve_site_used",
                severity=Severity.WARNING,
                stage=Stage.ALLOCATE,
                message="В сценарии нет штатных площадок, взлёт назначен с резервных",
            )
        )

    assignments: list[SortieAssignment] = []
    dropped: list[str] = []

    for chunk, instance in zip(
        _split_evenly(list(coverage.strips), len(instances)), instances, strict=True
    ):
        trip_index = 0
        current: list[Strip] = []

        for strip in chunk:
            candidate = [*current, strip]
            if fits(instance.vehicle, instance.site_id, candidate):
                current = candidate
                continue
            if current:
                if trip_index < instance.battery_sets:
                    assignments.append(_assignment(instance, current, trip_index))
                    trip_index += 1
                else:
                    dropped.extend(item.id for item in current)
                current = []
            if fits(instance.vehicle, instance.site_id, [strip]):
                current = [strip]
            else:
                dropped.append(strip.id)
                warnings.append(
                    PlanWarning(
                        code="allocate.strip_too_long",
                        severity=Severity.ERROR,
                        stage=Stage.ALLOCATE,
                        message=(
                            f"Галс {strip.id} длиной {strip.length_m / 1000:.1f} км не "
                            f"помещается в один вылет борта {instance.vehicle.name}. "
                            "Нужна декомпозиция области на подобласти (TODO(E3))"
                        ),
                        context={"strip_id": strip.id, "vehicle_id": instance.vehicle.id},
                    )
                )

        if current:
            if trip_index < instance.battery_sets:
                assignments.append(_assignment(instance, current, trip_index))
            else:
                dropped.extend(item.id for item in current)

    if dropped:
        warnings.append(
            PlanWarning(
                code="allocate.strips_unassigned",
                severity=Severity.ERROR,
                stage=Stage.ALLOCATE,
                message=(
                    f"{len(dropped)} галсов не распределены: не хватает вылетов при "
                    "заданном числе комплектов АКБ. Область останется неснятой"
                ),
                context={"strip_ids": ",".join(dropped[:20])},
            )
        )

    return Allocation(
        crs=coverage.crs,
        assignments=assignments,
        algorithm=ALGORITHM_NAME,
        alpha=scenario.alpha,
        seed=scenario.seed,
        warnings=warnings,
        stub=False,
    )


def _assignment(instance: _Instance, strips: list[Strip], trip_index: int) -> SortieAssignment:
    """Собрать назначение одного вылета."""
    return SortieAssignment(
        sortie_id=f"{instance.vehicle.id}-{instance.index}-t{trip_index}",
        vehicle_id=instance.vehicle.id,
        vehicle_instance=instance.index,
        site_id=instance.site_id,
        strip_ids=[strip.id for strip in strips],
        trip_index=trip_index,
    )


def fleet_units_for(scenario: Scenario, vehicle_id: str) -> list[FleetUnit]:
    """Строки флота, относящиеся к борту."""
    return [unit for unit in scenario.fleet if unit.vehicle_id == vehicle_id]


__all__ = ["ALGORITHM_NAME", "FitsPredicate", "build_baseline_allocation", "fleet_units_for"]
