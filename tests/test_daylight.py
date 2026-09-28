"""Съёмка только при Солнце не ниже порога (долг №55, заказчик №22).

Ограничение включается в сценарии (`min_sun_elevation_deg`): окна по высоте
Солнца, книга расписания переносит съёмку вылета в окно, краш-тест заново
считает высоту Солнца на каждом галсе.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from core.catalog import VehicleCatalog
from core.deconflict.schedule import daylight_shift
from core.flight.cost import retime_sortie
from core.geo.sun import daylight_windows, sun_elevation_deg
from core.pipeline import run_pipeline
from core.schemas import FlightPhase, MissionStatus
from core.validate import validate_plan
from tests.conftest import small_scenario

#: Центр области `real_msk` и начало работ сценария — 09:00 МСК 25.09.2026.
_MSK = (55.0, 38.6)
_START = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)


def test_sun_elevation_matches_the_review_numbers() -> None:
    """Ревью 2026-09-28: пик 34°, не ниже 25° — 09:35–14:59 МСК."""
    peak = max(
        sun_elevation_deg(_START + timedelta(minutes=minute), *_MSK) for minute in range(600)
    )
    assert peak == pytest.approx(34.07, abs=0.05)
    windows = daylight_windows(_START, *_MSK, 25.0, horizon_s=86400.0)
    assert len(windows) == 1
    opens, closes = windows[0]
    assert opens / 60 == pytest.approx(34, abs=2)  # 09:34 МСК
    assert closes / 60 == pytest.approx(359, abs=2)  # 14:59 МСК
    # Ночью Солнце под горизонтом.
    assert sun_elevation_deg(datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc), *_MSK) < 0


def test_window_already_open_starts_at_zero() -> None:
    windows = daylight_windows(_START, *_MSK, 20.0, horizon_s=86400.0)
    assert windows[0][0] == 0.0


def test_daylight_shift_moves_survey_into_next_window() -> None:
    windows = [(1000.0, 2000.0), (5000.0, 9000.0)]
    assert daylight_shift(1000.0, (100.0, 900.0), windows) == 0.0
    # Не помещается в остаток первого окна — во второе.
    assert daylight_shift(1500.0, (100.0, 900.0), windows) == pytest.approx(5000.0 - 1600.0)
    # Съёмка длиннее любого окна — никуда.
    assert daylight_shift(0.0, (0.0, 5000.0), windows) == float("inf")
    assert daylight_shift(0.0, None, windows) == 0.0
    assert daylight_shift(0.0, (0.0, 1.0), None) == 0.0


def test_threshold_needs_start_time() -> None:
    with pytest.raises(ValidationError, match="Солнца"):
        small_scenario(min_sun_elevation_deg=25.0)


def _early(threshold: float | None):
    """Маленький сценарий у Санкт-Петербурга с началом работ до рассвета."""
    return small_scenario(
        start_time="2026-09-25T03:00:00Z",
        min_sun_elevation_deg=threshold,
    )


def test_plan_waits_for_the_sun(catalog: VehicleCatalog) -> None:
    """Начало работ до рассвета: съёмка — только в окне, краш-тест это подтверждает."""
    free = run_pipeline(_early(None), catalog=catalog)
    bound = run_pipeline(_early(25.0), catalog=catalog)
    assert bound.mission.status is MissionStatus.FEASIBLE, [
        reason.message for reason in bound.mission.reasons
    ]
    assert bound.work_area.daylight
    opens = bound.work_area.daylight[0][0]
    assert opens > 3600.0
    first_strip = min(
        leg.waypoints[0].time_s
        for sortie in bound.mission.sorties
        for leg in sortie.legs
        if leg.phase is FlightPhase.STRIP
    )
    assert first_strip >= opens - 1.0
    assert free.mission.metrics and bound.mission.metrics
    assert bound.mission.metrics.makespan_s > free.mission.metrics.makespan_s + opens / 2


def test_crash_test_catches_survey_in_the_dark(catalog: VehicleCatalog) -> None:
    """Краш-тест независим: вылет, сдвинутый на ночь, — нарушение, а не предупреждение."""
    run = run_pipeline(_early(25.0), catalog=catalog)
    assert run.mission.status is MissionStatus.FEASIBLE
    sorties = list(run.mission.sorties)
    sorties[0] = retime_sortie(sorties[0], 0.0)
    report = validate_plan(
        run.scenario,
        run.work_area,
        run.result.decomposition,
        run.result.coverage,
        sorties,
        run.vehicles,
    )
    assert any(item.code == "validate.daylight" for item in report.violations)
