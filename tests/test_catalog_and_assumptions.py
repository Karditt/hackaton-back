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


def test_customer_values_cite_the_answer() -> None:
    """Значение заказчика ссылается на номер ответа и не выдаётся за допущение команды."""
    values = assumptions.customer_values()
    assert values
    for item in values:
        assert "№" in item.source, f"{item.name}: нет ссылки на ответ заказчика"
        assert not item.is_team_assumption()
    assert pytest.approx(0.20) == assumptions.BATTERY_RESERVE_FRACTION
    assert pytest.approx(300.0) == assumptions.SEPARATION_FIXED_WING_M
    assert pytest.approx(50.0) == assumptions.SEPARATION_MULTIROTOR_M


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


def test_catalog_loads_three_vehicles(catalog: VehicleCatalog) -> None:
    """В каталоге ровно те борта, что описаны в исходном ТЗ."""
    assert {vehicle.id for vehicle in catalog} == {"geoscan-201", "geoscan-801", "geoscan-gemini"}


def test_unknown_vehicle_raises(catalog: VehicleCatalog) -> None:
    """Борт вне каталога — ошибка, а не подстановка «похожего»."""
    with pytest.raises(CatalogError):
        catalog.get("dji-phantom")


def test_derived_fields_are_marked(catalog: VehicleCatalog) -> None:
    """Инвариант 6: выведенное допущением обязано быть помечено как выведенное."""
    for vehicle in catalog:
        assert "cruise_speed_mps" in vehicle.derived_fields
        assert "battery_reserve_fraction" in vehicle.derived_fields
    assert "turn_radius_m" in catalog.get("geoscan-201").derived_fields


def test_gaps_are_preserved_not_filled(catalog: VehicleCatalog) -> None:
    """Пробел каталога переносится в модель, а не затирается числом."""
    assert any("площад" in gap.lower() for gap in catalog.get("geoscan-gemini").gaps)
    multirotor_801 = catalog.get("geoscan-801")
    assert multirotor_801.energy_wh is None
    assert any("АКБ" in gap for gap in multirotor_801.gaps)
    sony = catalog.get("geoscan-201").payload("sony-zv-e10")
    assert sony is not None and sony.gaps, "без фокусного и угла поля зрения высоту не посчитать"


def test_fixed_wing_speeds(catalog: VehicleCatalog) -> None:
    """У самолёта есть минимальная скорость — нужна для проверки попутного ветра (№45)."""
    plane = catalog.get("geoscan-201")
    assert plane.type is VehicleType.FIXED_WING
    assert plane.turn_radius_m > 0.0
    assert plane.min_speed_mps is not None
    assert plane.min_speed_mps < plane.cruise_speed_mps < (plane.max_speed_mps or 0)
    assert catalog.get("geoscan-gemini").turn_radius_m == 0.0


def test_pollux_optics_are_complete(catalog: VehicleCatalog) -> None:
    """Pollux — полная оптика у обоих бортов: размер матрицы — тождество мкм × пиксели."""
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
