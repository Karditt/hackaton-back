"""Сценарии в формате заказчика: исходные файлы и собранный JSON (E1, E10).

Каждый сценарий манифеста `scenarios/sources/manifest.json` — это три слоя
KML и параметры задания, как их отдаёт заказчик. JSON сценария обязан
собираться из них: иначе интерфейс показывал бы одни файлы, а считал другое.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.export.input_layers import export_input_layers, zones_kml
from core.geo.kml_import import read_zones
from core.sources import (
    ScenarioSources,
    build_from_sources,
    load_sources,
    scenario_differences,
)
from tests.conftest import SCENARIO_NAMES, load_scenario, small_scenario

SOURCES = load_sources()


def test_every_test_scenario_has_its_files() -> None:
    """У каждого сценария из `scenarios/` есть исходники; сценарий заказчика — тоже."""
    assert set(SCENARIO_NAMES) | {"real_msk"} <= set(SOURCES)
    assert not SOURCES["real_msk"].planning_available, "расчёт на данных заказчика пока долгий"
    assert SOURCES["real_msk"].files["area"].name == "survey_area_msk.kml"


@pytest.mark.parametrize("scenario_id", sorted(SOURCES))
def test_scenario_json_is_built_from_its_files(scenario_id: str) -> None:
    """JSON сценария — ровно то, что собирается из его файлов."""
    item = SOURCES[scenario_id]
    built, report = build_from_sources(item)
    assert scenario_differences(built, load_scenario(item.scenario)) == []
    assert report.area.kept == len(built.area.polygons)


def _roundtrip(scenario, tmp_path: Path):
    files = export_input_layers(scenario, tmp_path)
    sources = ScenarioSources(scenario_id=scenario.id, scenario=tmp_path / "x.json", files=files)
    return build_from_sources(sources)[0]


def test_export_and_import_give_the_same_scenario(tmp_path: Path) -> None:
    """Выгрузка в формат заказчика и чтение обратно не меняют сценарий.

    Сценарий нарочно неудобный: зона с окном без конца, зона выше 300 м без
    потолка, препятствие-точка и препятствие-контур.
    """
    scenario = small_scenario(
        no_fly_zones=[
            {
                "id": "open-window",
                "name": "Работы с 07:00",
                "polygon": [[[30.301, 59.901], [30.303, 59.901], [30.303, 59.903]]],
                "active_from": "2026-09-20T07:00:00Z",
            },
            {
                "id": "high",
                "name": "Выше 300 м",
                "polygon": [[[30.310, 59.905], [30.312, 59.905], [30.312, 59.907]]],
                "min_alt_m": 300,
            },
        ],
        obstacles=[
            {"id": "mast", "name": "Мачта", "position": [30.305, 59.904], "height_m": 120.5},
            {
                "id": "hangar",
                "name": "Ангар",
                "polygon": [[[30.315, 59.902], [30.316, 59.902], [30.316, 59.903]]],
                "height_m": 25,
            },
        ],
        start_time="2026-09-20T06:00:00Z",
    )
    built = _roundtrip(scenario, tmp_path)
    assert scenario_differences(built, scenario) == []
    zones = {zone.id: zone for zone in built.no_fly_zones}
    assert zones["open-window"].active_to is None
    assert zones["high"].min_alt_m == 300 and zones["high"].max_alt_m is None
    assert next(item for item in built.obstacles if item.id == "mast").position is not None


def test_several_windows_split_the_zone(tmp_path: Path) -> None:
    """Контракт хранит одно окно на зону: два окна — две записи с общим именем."""
    scenario = small_scenario(
        no_fly_zones=[
            {
                "id": "orvd",
                "name": "ОРВД",
                "polygon": [[[30.301, 59.901], [30.303, 59.901], [30.303, 59.903]]],
                "active_from": "2026-09-20T07:00:00Z",
                "active_to": "2026-09-20T09:00:00Z",
            }
        ]
    )
    text = (
        (zones_kml(scenario) or b"")
        .decode("utf-8")
        .replace(
            "2026-09-20T07:00:00Z/2026-09-20T09:00:00Z",
            "2026-09-20T07:00:00Z/2026-09-20T09:00:00Z;2026-09-21T07:00:00Z/2026-09-21T09:00:00Z",
        )
    )
    zones, _ = read_zones(text)
    assert [zone.id for zone in zones] == ["orvd-w1", "orvd-w2"]
    assert {zone.name for zone in zones} == {"ОРВД"}
    assert zones[1].active_from == datetime(2026, 9, 21, 7, tzinfo=timezone.utc)


def test_unreadable_window_keeps_the_zone_always_active() -> None:
    """Нечитаемое окно — зона действует всегда и об этом сказано: сторона безопасная."""
    scenario = small_scenario(
        no_fly_zones=[
            {
                "id": "orvd",
                "polygon": [[[30.301, 59.901], [30.303, 59.901], [30.303, 59.903]]],
                "active_from": "2026-09-20T07:00:00Z",
                "active_to": "2026-09-20T09:00:00Z",
            }
        ]
    )
    text = (
        (zones_kml(scenario) or b"")
        .decode("utf-8")
        .replace("2026-09-20T07:00:00Z/2026-09-20T09:00:00Z", "завтра с утра")
    )
    zones, report = read_zones(text)
    assert zones[0].is_permanent()
    assert any(item.code == "import.zone_window_unparsed" for item in report.warnings)
