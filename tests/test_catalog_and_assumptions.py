"""Инварианты 5 и 6: константы — только в реестре, ТТХ — только из каталога."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from core import assumptions
from core.assumptions import Kind
from core.catalog import DEFAULT_CATALOG_PATH, VehicleCatalog, load_catalog
from core.cli import REGISTRY_BEGIN, REGISTRY_END, registry_markdown
from core.errors import CatalogError
from core.schemas import VehicleType
from core.survey.plan import build_survey_plan
from tests.conftest import small_scenario


def test_every_assumption_has_a_source() -> None:
    """Инвариант 5: константа без источника — дефект самой константы."""
    for item in assumptions.all_assumptions():
        assert item.source.strip(), f"{item.name}: пустой источник"
        assert item.unit.strip(), f"{item.name}: не указана единица измерения"


def test_known_limitations_document_follows_the_registry() -> None:
    """E13: раздел реестра в «известных ограничениях» совпадает с `core/assumptions.py`.

    Раздел собирается командой `assumptions --update`; новое допущение без
    обновления документа — дефект документации.
    """
    text = (Path(__file__).parent.parent / "docs" / "known_limitations.md").read_text(
        encoding="utf-8"
    )
    begin = text.index(REGISTRY_BEGIN) + len(REGISTRY_BEGIN)
    end = text.index(REGISTRY_END)
    assert text[begin:end].strip() == registry_markdown().strip(), (
        "обновите: python -m core.cli assumptions --update docs/known_limitations.md"
    )
    for item in assumptions.team_assumptions():
        assert f"`{item.name}`" in text


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


def test_catalog_holds_geoscan_fleet(catalog: VehicleCatalog) -> None:
    """Борта из ТЗ и выпускаемые съёмочные борта Геоскана; снятый с производства 701 — нет."""
    assert {vehicle.id for vehicle in catalog} == {
        "geoscan-201",
        "geoscan-201-t",
        "geoscan-401",
        "geoscan-801",
        "geoscan-gemini",
        "geoscan-lite",
    }


def test_unknown_vehicle_raises(catalog: VehicleCatalog) -> None:
    """Борт вне каталога — ошибка, а не подстановка «похожего»."""
    with pytest.raises(CatalogError):
        catalog.get("dji-phantom")


def test_derived_fields_are_marked(catalog: VehicleCatalog) -> None:
    """Инвариант 6: выведенное допущением обязано быть помечено как выведенное."""
    for vehicle in catalog:
        assert "battery_reserve_fraction" in vehicle.derived_fields
        if vehicle.is_fixed_wing():
            assert "turn_radius_m" in vehicle.derived_fields
    for vehicle_id in ("geoscan-201", "geoscan-401", "geoscan-801", "geoscan-gemini"):
        assert "cruise_speed_mps" in catalog.get(vehicle_id).derived_fields


def test_published_cruise_speed_is_not_derived(catalog: VehicleCatalog) -> None:
    """Крейсерская скорость с сайта производителя (80 км/ч) — ТТХ, а не допущение."""
    for vehicle_id in ("geoscan-lite", "geoscan-201-t"):
        vehicle = catalog.get(vehicle_id)
        assert "cruise_speed_mps" not in vehicle.derived_fields
        assert vehicle.cruise_speed_mps == pytest.approx(80.0 / 3.6, abs=0.1)


def test_gaps_are_preserved_not_filled(catalog: VehicleCatalog) -> None:
    """Пробел каталога переносится в модель, а не затирается числом."""
    for vehicle_id in ("geoscan-201", "geoscan-lite"):
        sony = catalog.get(vehicle_id).payload("sony-zv-e10")
        assert sony is not None and sony.focal_length_mm is None
        assert any(gap.startswith("focal_length_mm") for gap in sony.gaps)
    thermal = catalog.get("geoscan-201-t").payload("vlm640-t")
    assert thermal is not None and thermal.focal_length_mm is None
    assert any(gap.startswith("focal_length_mm") for gap in thermal.gaps)
    thermal_801 = catalog.get("geoscan-801").payload("geoscan-801-thermal")
    assert thermal_801 is not None and thermal_801.sensor_width_mm is None
    assert thermal_801.gaps, "без размера пикселя ширину кадра на местности не посчитать"


def test_documented_battery_energy_is_taken_as_is(catalog: VehicleCatalog) -> None:
    """Энергия АКБ 801 — прямо из руководства, стр. 90, а не расчёт."""
    assert catalog.get("geoscan-801").energy_wh == pytest.approx(126.28)


def test_sony_optics_come_from_the_camera_maker(catalog: VehicleCatalog) -> None:
    """RX1R II и III: объектив 35 мм и кадр по паспорту Sony — ничего не выведено."""
    rx1rm2 = catalog.get("geoscan-lite").payload("sony-rx1rm2")
    rx1rm3 = catalog.get("geoscan-401").payload("sony-rx1rm3")
    assert rx1rm2 is not None and rx1rm3 is not None
    assert (rx1rm2.image_width_px, rx1rm2.focal_length_mm) == (7952, pytest.approx(35.0))
    assert (rx1rm3.image_width_px, rx1rm3.focal_length_mm) == (9504, pytest.approx(35.0))
    assert not rx1rm2.derived_fields and not rx1rm3.derived_fields


def test_megapixel_range_is_rejected(tmp_path) -> None:
    """Диапазон мегапикселей — две камеры под одним именем; выбирать границу молча нельзя."""
    path = tmp_path / "vehicles.yaml"
    path.write_text(
        """
vehicles:
  - id: plane
    type: fixed_wing
    airspeed_min_mps: {value: 18, source: t}
    airspeed_max_mps: {value: 36, source: t}
    endurance_s: {value: 3600, source: t}
    max_wind_mps: {value: 12, source: t}
    payloads:
      - {id: cam, kind: rgb, source: t, resolution_mp: [45, 61], focal_length_mm: 40}
""",
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="диапазон мегапикселей"):
        load_catalog(path)


def test_modeled_area_is_close_to_manufacturer_claims(catalog: VehicleCatalog) -> None:
    """Правдоподобие модели: площадь за вылет против заявленной производителем.

    Верхняя оценка модели — весь полезный заряд (до отсечки 20%) на галсе,
    без разворотов, подлёта, набора и снижения. Она не должна быть заметно
    ниже заявленной (иначе модель пессимистичнее производителя — например,
    занижена скорость) и не должна превышать её вдвое (накладные расходы
    вылета не съедают больше половины времени). Числа производителя — с той
    камерой, с которой их заявляют, и без ветра.
    """
    raw = yaml.safe_load(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
    checked = 0
    for entry in raw["vehicles"]:
        claims = (entry.get("survey_area_per_sortie") or {}).get("value") or []
        vehicle = catalog.get(entry["id"])
        for claim in claims:
            payload = vehicle.payload(claim["payload_id"])
            assert payload is not None, f"{vehicle.id}: нет камеры {claim['payload_id']}"
            scenario = small_scenario(
                survey_type=payload.kind.value,
                quality={"gsd_m": claim["gsd_m"]},
                wind={"speed_mps": 0.0, "direction_deg": 0.0},
                fleet=[{"vehicle_id": vehicle.id, "payload_id": payload.id}],
            )
            params = build_survey_plan(scenario, {vehicle.id: vehicle}).per_vehicle[0]
            usable_s = vehicle.endurance_s * (1.0 - vehicle.battery_reserve_fraction)
            modeled_km2 = usable_s * params.survey_speed_mps * params.strip_spacing_m / 1.0e6
            ratio = modeled_km2 / claim["area_km2"]
            assert 0.8 <= ratio <= 2.0, (
                f"{vehicle.id} / {payload.id} при {claim['gsd_m'] * 100:.0f} см/пикс: "
                f"модель {modeled_km2:.2f} км², производитель {claim['area_km2']} км²"
            )
            checked += 1
    assert checked >= 8


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
