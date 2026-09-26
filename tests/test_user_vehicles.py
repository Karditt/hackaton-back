"""ТТХ, заданные пользователем (решение владельца, 2026-09-26).

Сценарий несёт свои записи `Scenario.vehicles` поверх каталога: правку модели
каталога или новую модель. Проверяется, что они действительно идут в расчёт,
что каталог при этом не меняется (к исходным данным можно вернуться) и что
план честно говорит, какие борта летят не по данным производителя.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.catalog import VehicleCatalog
from core.errors import CatalogError
from core.pipeline import check_scenario, run_pipeline, scenario_catalog
from core.schemas import Scenario
from tests.conftest import small_scenario


def _gemini(catalog: VehicleCatalog, **update) -> dict:
    """Запись Геоскан Gemini из каталога как словарь контракта, с правками."""
    record = catalog.get("geoscan-gemini").model_dump(mode="json")
    record.update(update)
    return record


def test_edited_catalog_model_goes_into_the_plan(catalog: VehicleCatalog) -> None:
    """Правка ТТХ модели каталога меняет план, а сам каталог остаётся прежним."""
    base = small_scenario()
    original = catalog.get("geoscan-gemini").endurance_s
    shorter = _gemini(catalog, endurance_s=original / 3, user_fields=["endurance_s"])
    edited = small_scenario(vehicles=[shorter])

    before = run_pipeline(base, catalog=catalog)
    after = run_pipeline(edited, catalog=catalog)

    assert before.mission.is_feasible() and after.mission.is_feasible()
    assert len(after.mission.sorties) > len(before.mission.sorties), (
        "втрое короче вылет — больше вылетов на ту же площадь"
    )
    assert catalog.get("geoscan-gemini").endurance_s == original, "каталог не изменился"
    assert after.vehicles["geoscan-gemini"].endurance_s == pytest.approx(original / 3)

    warnings = [item for item in after.mission.warnings if item.code == "catalog.user_vehicle"]
    assert len(warnings) == 1
    assert "продолжительность полёта" in warnings[0].message
    assert not any(item.code == "catalog.user_vehicle" for item in before.mission.warnings)


def test_new_model_is_planned_under_its_own_id(catalog: VehicleCatalog) -> None:
    """Модель, которой нет в каталоге, считается под своим идентификатором."""
    custom = _gemini(
        catalog,
        id="user-model-test",
        name="Свой квадрокоптер",
        user_fields=["cruise_speed_mps", "endurance_s", "max_wind_mps"],
    )
    scenario = small_scenario(
        vehicles=[custom],
        fleet=[{"vehicle_id": "user-model-test", "count": 1, "payload_id": "geoscan-pf1b"}],
    )
    run = run_pipeline(scenario, catalog=catalog)
    assert run.mission.is_feasible()
    assert {sortie.vehicle_id for sortie in run.mission.sorties} == {"user-model-test"}


def test_battery_cutoff_stays_the_customer_value(catalog: VehicleCatalog) -> None:
    """Отсечку заряда пользователь не задаёт: она одна на все борта."""
    greedy = _gemini(catalog, battery_reserve_fraction=0.01, user_fields=["endurance_s"])
    merged = scenario_catalog(small_scenario(vehicles=[greedy]), catalog)
    vehicle = merged.get("geoscan-gemini")
    assert (
        vehicle.battery_reserve_fraction == catalog.get("geoscan-gemini").battery_reserve_fraction
    )
    assert "battery_reserve_fraction" in vehicle.derived_fields
    assert merged.user_ids == {"geoscan-gemini"}


def test_contradictory_user_numbers_are_rejected(catalog: VehicleCatalog) -> None:
    """Противоречивые ТТХ — ошибка входа с объяснением, а не план."""
    too_fast = _gemini(catalog, cruise_speed_mps=40.0, max_speed_mps=15.0)
    with pytest.raises(CatalogError, match="больше максимальной"):
        check_scenario(small_scenario(vehicles=[too_fast]), catalog)

    plane = catalog.get("geoscan-201").model_dump(mode="json")
    plane.update(turn_radius_m=0.0)
    scenario = small_scenario(vehicles=[plane], fleet=[{"vehicle_id": "geoscan-201", "count": 1}])
    with pytest.raises(CatalogError, match="радиус разворота"):
        check_scenario(scenario, catalog)


def test_user_models_must_have_distinct_ids(catalog: VehicleCatalog) -> None:
    """Две записи с одним id — неоднозначно, какую считать."""
    record = _gemini(catalog)
    with pytest.raises(ValidationError, match="повторяются"):
        Scenario.model_validate(
            {**small_scenario().model_dump(mode="json"), "vehicles": [record, record]}
        )
