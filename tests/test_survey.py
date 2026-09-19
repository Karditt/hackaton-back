"""Модель съёмки по описанию заказчика (№25, №26, №45)."""

from __future__ import annotations

import math

import pytest

from core.catalog import VehicleCatalog
from core.errors import ScenarioError
from core.schemas import Payload, PayloadKind, SurveyType
from core.survey.plan import altitude_for_gsd, build_survey_plan, ground_speed_band_mps
from core.survey.presets import load_presets
from tests.conftest import small_scenario


def _payload(**fields) -> Payload:
    return Payload(id="cam", name="cam", kind=PayloadKind.RGB, source="test", **fields)


def test_altitude_from_fov_matches_altitude_from_optics() -> None:
    """GSD → высота: по углу поля зрения и по фокусному — одно и то же (№26)."""
    by_optics = _payload(image_width_px=6000, sensor_width_mm=23.5, focal_length_mm=20.0)
    fov_deg = math.degrees(2.0 * math.atan(23.5 / 2.0 / 20.0))
    by_fov = _payload(image_width_px=6000, fov_horizontal_deg=fov_deg)
    assert altitude_for_gsd(by_fov, 0.05) == pytest.approx(altitude_for_gsd(by_optics, 0.05))


def test_gemini_pf1b_altitude_is_physical() -> None:
    """PF1B: 5 см/пикс на 20 мм и 23,5 мм матрицы — около 255 м."""
    payload = _payload(image_width_px=6000, sensor_width_mm=23.5, focal_length_mm=20.0)
    assert altitude_for_gsd(payload, 0.05) == pytest.approx(0.05 * 20.0 * 6000 / 23.5)


def test_riebo_frame_size_is_derived_and_marked(catalog: VehicleCatalog) -> None:
    """Riebo R4: пиксели выводятся из 45 Мп и сторон полного кадра 3:2 — и помечаются."""
    riebo = catalog.get("geoscan-201").payload("riebo-r4")
    assert riebo is not None
    assert riebo.image_width_px == pytest.approx(math.sqrt(45e6 * 1.5), rel=0.01)
    assert {"image_width_px", "image_height_px"} <= set(riebo.derived_fields)


def test_riebo_altitude_follows_focal_length_not_rejected_fov(catalog: VehicleCatalog) -> None:
    """Riebo: высота по фокусному 40 мм и полному кадру; углы 89,84° документа отвергнуты.

    По углам вышло бы в 2,2 раза ниже: 2·tg(44,92°) ≈ 1,99 против 36 / 40 = 0,9.
    """
    riebo = catalog.get("geoscan-201").payload("riebo-r4")
    assert riebo is not None
    assert riebo.fov_horizontal_deg is None
    assert riebo.focal_length_mm == pytest.approx(40.0)
    assert riebo.sensor_width_mm == pytest.approx(36.0)
    assert {"sensor_width_mm", "sensor_height_mm"} <= set(riebo.derived_fields)
    expected_m = 0.05 * riebo.image_width_px * 40.0 / 36.0
    assert altitude_for_gsd(riebo, 0.05) == pytest.approx(expected_m)
    assert 450.0 < altitude_for_gsd(riebo, 0.05) < 460.0


def test_presets_cover_every_survey_type_with_sources() -> None:
    """Заказчик, №25: отдельный конфиг перекрытий на каждый тип съёмки."""
    presets = load_presets()
    assert set(presets) == set(SurveyType)
    for preset in presets.values():
        assert all(source.strip() for source in preset.sources.values())
    assert presets[SurveyType.RGB].min_side_overlap == pytest.approx(0.5)


def test_overlap_below_minimum_is_rejected(catalog: VehicleCatalog) -> None:
    """Меньше 50% для аэрофотосъёмки «обычно не летают» — карта не сошьётся (№25)."""
    scenario = small_scenario(quality={"gsd_m": 0.05, "side_overlap": 0.3})
    with pytest.raises(ScenarioError):
        build_survey_plan(scenario, {"geoscan-gemini": catalog.get("geoscan-gemini")})


def test_wind_lowers_ground_speed_instead_of_changing_time(catalog: VehicleCatalog) -> None:
    """№45: автопилот держит путевую скорость — при сильном ветре она ниже, а не «плавает»."""
    gemini = catalog.get("geoscan-gemini")
    calm = build_survey_plan(
        small_scenario(wind={"speed_mps": 0, "direction_deg": 0}), {gemini.id: gemini}
    )
    windy = build_survey_plan(
        small_scenario(wind={"speed_mps": 8, "direction_deg": 0}), {gemini.id: gemini}
    )
    assert calm.per_vehicle[0].survey_speed_mps == pytest.approx(gemini.cruise_speed_mps)
    assert windy.per_vehicle[0].survey_speed_mps == pytest.approx(15.0 - 8.0)


def test_fixed_wing_has_no_speed_in_strong_wind(catalog: VehicleCatalog) -> None:
    """Самолёт по ветру не может лететь медленнее минимальной скорости: при 11 м/с диапазона нет."""
    plane = catalog.get("geoscan-201")
    lower, upper = ground_speed_band_mps(plane, 11.0)
    assert lower > upper
    scenario = small_scenario(
        wind={"speed_mps": 11.0, "direction_deg": 90.0},
        fleet=[{"vehicle_id": "geoscan-201", "payload_id": "riebo-r4"}],
    )
    plan = build_survey_plan(scenario, {plane.id: plane})
    assert not plan.per_vehicle
    assert any(w.code == "survey.wind_speed_band_empty" for w in plan.warnings)


def test_corridor_caps_survey_altitude(catalog: VehicleCatalog) -> None:
    """Съёмка не выше коридора минус эшелон перелёта (№10)."""
    gemini = catalog.get("geoscan-gemini")
    plan = build_survey_plan(
        small_scenario(altitude_corridor={"max_altitude_agl_m": 200}), {gemini.id: gemini}
    )
    assert plan.per_vehicle[0].altitude_agl_m <= 200 - 30 + 1e-6


def test_inspection_camera_is_excluded_from_area_survey(catalog: VehicleCatalog) -> None:
    """Геоскан 801 с зум-камерой не годится для площадной RGB-съёмки — исключён с причиной."""
    vehicle = catalog.get("geoscan-801")
    plan = build_survey_plan(
        small_scenario(fleet=[{"vehicle_id": "geoscan-801"}]), {vehicle.id: vehicle}
    )
    assert not plan.per_vehicle
    assert plan.warnings
