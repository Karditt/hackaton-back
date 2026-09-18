"""Развёртка состава флота в физические борта."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.schemas import Scenario, Vehicle


@dataclass(frozen=True)
class FleetInstance:
    """Один физический борт: модель, номер экземпляра, площадка, предел вылетов."""

    vehicle: Vehicle
    index: int
    """Номер экземпляра среди бортов этой модели, с нуля. Уникален в паре с моделью."""
    site_id: str | None
    """Площадка, к которой борт приписан сценарием; None — выбирает распределение."""
    battery_sets: int | None
    """Предел числа вылетов; None — не ограничено."""

    @property
    def key(self) -> tuple[str, int]:
        """Уникальный ключ борта: (модель, номер экземпляра)."""
        return self.vehicle.id, self.index

    @property
    def label(self) -> str:
        """Подпись борта для сообщений."""
        return f"{self.vehicle.name} #{self.index + 1}"


def expand_fleet(
    scenario: Scenario, vehicles: dict[str, Vehicle], usable: Iterable[str]
) -> list[FleetInstance]:
    """Развернуть состав флота в список отдельных бортов.

    Args:
        scenario: сценарий.
        vehicles: каталог бортов.
        usable: идентификаторы бортов, пригодных для данного типа съёмки.

    Returns:
        Физические борта. Площадка заполнена, только если её задал сценарий.
    """
    allowed = set(usable)
    counters: dict[str, int] = {}
    instances: list[FleetInstance] = []
    for unit in scenario.fleet:
        if unit.vehicle_id not in allowed:
            continue
        for _ in range(unit.count):
            index = counters.get(unit.vehicle_id, 0)
            counters[unit.vehicle_id] = index + 1
            instances.append(
                FleetInstance(
                    vehicle=vehicles[unit.vehicle_id],
                    index=index,
                    site_id=unit.site_id,
                    battery_sets=unit.battery_sets,
                )
            )
    return instances


__all__ = ["FleetInstance", "expand_fleet"]
