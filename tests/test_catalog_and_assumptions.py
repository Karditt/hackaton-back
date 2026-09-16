"""Инварианты 5 и 6: константы — только в реестре, ТТХ — только из каталога."""

from __future__ import annotations

import math

import pytest

from core import assumptions
from core.assumptions import Kind
from core.catalog import VehicleCatalog, load_catalog
from core.errors import CatalogError
from core.schemas import VehicleType


def test_every_assumption_has_a_source() -> None:
    """Инвариант 5: константа без источника — дефект самой константы."""
    for item in assumptions.all_assumptions():
        assert item.source.strip(), f"{item.name}: пустой источник"
        assert item.unit.strip(), f"{item.name}: не указана единица измерения"


def test_team_assumptions_explain_themselves() -> None:
    """У допущения команды обязано быть объяснение: оно уходит в документацию."""
    for item in assumptions.team_assumptions():
        assert item.kind is Kind.TEAM
        assert item.rationale.strip(), f"{item.name}: допущение без обоснования"


def test_registry_has_no_duplicates() -> None:
    """Повторная регистрация имени запрещена на уровне реестра."""
    names = [item.name for item in assumptions.all_assumptions()]
    assert len(names) == len(set(names))
    with pytest.raises(ValueError):
        assumptions._register("G_MPS2", 1.0, "—", Kind.TEAM, "дубликат")


def test_turn_radius_follows_bank_angle_formula() -> None:
    """r = V² / (g · tan φ) — формула, а не подобранное число."""
    speed_mps = 27.0
    expected = speed_mps**2 / (
        assumptions.G_MPS2 * math.tan(math.radians(assumptions.FIXED_WING_BANK_ANGLE_DEG))
    )
    assert assumptions.fixed_wing_turn_radius_m(speed_mps) == pytest.approx(expected)


def test_turn_radius_grows_with_speed() -> None:
    """Быстрее летишь — шире разворот. Проверка знака, а не значения."""
    assert assumptions.fixed_wing_turn_radius_m(30.0) > assumptions.fixed_wing_turn_radius_m(20.0)


def test_catalog_loads_three_vehicles(catalog: VehicleCatalog) -> None:
    """В каталоге ровно те борта, что описаны в исходном ТЗ."""
    ids = {vehicle.id for vehicle in catalog}
    assert ids == {"geoscan-201", "geoscan-801", "geoscan-gemini"}


def test_unknown_vehicle_raises(catalog: VehicleCatalog) -> None:
    """Борт вне каталога — ошибка, а не подстановка «похожего»."""
    with pytest.raises(CatalogError):
        catalog.get("dji-phantom")


def test_derived_fields_are_marked(catalog: VehicleCatalog) -> None:
    """Инвариант 6: выведенное допущением обязано быть помечено как выведенное.

    Крейсерской скорости нет ни у одного из трёх бортов, радиуса разворота
    нет у Геоскан 201 — всё это выводится правилами из assumptions.py.
    """
    for vehicle in catalog:
        assert "cruise_speed_mps" in vehicle.derived_fields
        assert "battery_reserve_fraction" in vehicle.derived_fields
    assert "turn_radius_m" in catalog.get("geoscan-201").derived_fields


def test_gaps_are_preserved_not_filled(catalog: VehicleCatalog) -> None:
    """Пробел каталога переносится в модель, а не затирается числом."""
    gemini = catalog.get("geoscan-gemini")
    assert any("площад" in gap.lower() for gap in gemini.gaps)

    multirotor_801 = catalog.get("geoscan-801")
    assert multirotor_801.energy_wh is None
    assert any("АКБ" in gap for gap in multirotor_801.gaps)


def test_fixed_wing_turn_radius_is_positive(catalog: VehicleCatalog) -> None:
    """У самолётного типа радиус разворота ненулевой, у мультиротора — ноль."""
    assert catalog.get("geoscan-201").turn_radius_m > 0.0
    assert catalog.get("geoscan-gemini").turn_radius_m == 0.0
    assert catalog.get("geoscan-201").type is VehicleType.FIXED_WING
    assert catalog.get("geoscan-gemini").type is VehicleType.MULTIROTOR


def test_cruise_speed_is_below_maximum(catalog: VehicleCatalog) -> None:
    """Крейсерская скорость выводится ниже максимальной, а не равна ей."""
    for vehicle in catalog:
        if vehicle.max_speed_mps is not None:
            assert vehicle.cruise_speed_mps <= vehicle.max_speed_mps


def test_pollux_optics_are_complete(catalog: VehicleCatalog) -> None:
    """Pollux — единственная нагрузка с полной оптикой у обоих бортов."""
    for vehicle_id in ("geoscan-201", "geoscan-gemini"):
        payload = catalog.get(vehicle_id).payload("pollux")
        assert payload is not None
        assert payload.focal_length_mm == pytest.approx(8.0)
        assert payload.sensor_width_mm == pytest.approx(1440 * 3.45e-3)
        assert payload.image_width_px == 1440


def test_missing_catalog_file_raises() -> None:
    """Отсутствующий каталог — понятная ошибка, а не трассировка."""
    with pytest.raises(CatalogError):
        load_catalog("no/such/vehicles.yaml")
