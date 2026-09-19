"""Развёртка состава флота в физические борта."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.schemas import Scenario, Vehicle


def board_id(vehicle_id: str, vehicle_instance: int) -> str:
    """Идентификатор борта: `<модель>_<номер экземпляра с единицы>`.

    Так борт подписан для людей — в интерфейсе, в выгрузке, в имени файла его
    задания (docs/export_format.md §1): `geoscan-gemini_3` — третий Gemini.

    Args:
        vehicle_id: модель из каталога.
        vehicle_instance: номер экземпляра внутри модели, с нуля.

    Returns:
        Идентификатор борта.
    """
    return f"{vehicle_id}_{vehicle_instance + 1}"


def sortie_id(vehicle_id: str, vehicle_instance: int, trip_index: int) -> str:
    """Идентификатор вылета: `<борт>-t<номер вылета с единицы>`.

    Номера — те же, что видит человек: `geoscan-gemini_3-t1` — первый вылет
    третьего Gemini.

    Args:
        vehicle_id: модель из каталога.
        vehicle_instance: номер экземпляра внутри модели, с нуля.
        trip_index: номер вылета этого борта, с нуля.

    Returns:
        Идентификатор вылета.
    """
    return f"{board_id(vehicle_id, vehicle_instance)}-t{trip_index + 1}"


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
    def board_id(self) -> str:
        """Идентификатор борта для людей и файлов: `geoscan-gemini_3`."""
        return board_id(self.vehicle.id, self.index)

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


__all__ = ["FleetInstance", "board_id", "expand_fleet", "sortie_id"]
