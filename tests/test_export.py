"""Экспорт: вторая граница инварианта 1 и формат из docs/export_format.md (№21)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from core.export import export_mission, mission_to_geojson, mission_to_kml, schedule_to_csv
from core.pipeline import PlanningRun
from core.schemas import ExportFormat

_KML_NS = "{http://www.opengis.net/kml/2.2}"


def _geojson(run: PlanningRun) -> dict:
    return mission_to_geojson(run.result, run.scenario, run.work_area.projector)


def _first_coordinate(coordinates) -> list[float]:
    item = coordinates
    while isinstance(item[0], list):
        item = item[0]
    return item


def test_geojson_coordinates_are_wgs84(run: PlanningRun) -> None:
    """Наружу уходят градусы, а не метры рабочей проекции."""
    document = _geojson(run)
    assert document["type"] == "FeatureCollection"
    for feature in document["features"]:
        lon, lat = _first_coordinate(feature["geometry"]["coordinates"])[:2]
        assert -180.0 <= lon <= 180.0
        assert -90.0 <= lat <= 90.0


def test_geojson_carries_status_and_altitude_reference(run: PlanningRun) -> None:
    """Статус, режим высоты (AGL, relativeToGround) и признак заглушек — в свойствах."""
    properties = _geojson(run)["properties"]
    assert properties["status"] == run.mission.status.value
    assert properties["altitude_mode"] == "relativeToGround"
    assert properties["altitude_reference"] == "AGL"
    assert properties["stub_stages"]


def test_feasible_export_has_routes_cells_and_schedule(runs: dict[str, PlanningRun]) -> None:
    """№21: линии маршрута плюс полигоны подобластей; метки времени; расписание."""
    document = _geojson(runs["demo_100km2"])
    kinds = {feature["properties"].get("kind") for feature in document["features"]}
    assert {"leg", "survey_cell", "maneuver_zone", "obstacle", "no_fly_zone", "site"} <= kinds
    legs = [f for f in document["features"] if f["properties"]["kind"] == "leg"]
    assert all(f["properties"]["start_time"] for f in legs), (
        "начало работ задано — время абсолютное"
    )
    assert len(document["properties"]["schedule"]) == len(runs["demo_100km2"].mission.sorties)


def test_infeasible_export_is_a_report_without_routes(runs: dict[str, PlanningRun]) -> None:
    """Инвариант 10: у невыполнимого плана в выгрузке нет маршрутов — есть причины и советы."""
    document = _geojson(runs["nasty"])
    kinds = {feature["properties"].get("kind") for feature in document["features"]}
    assert "leg" not in kinds
    assert document["properties"]["status"] == "infeasible"
    assert document["properties"]["reasons"]
    assert document["properties"]["recommendations"]


def test_geojson_linestrings_have_two_distinct_points(run: PlanningRun) -> None:
    """RFC 7946: LineString из двух совпадающих точек невалиден."""
    for feature in _geojson(run)["features"]:
        if feature["geometry"]["type"] != "LineString":
            continue
        coords = feature["geometry"]["coordinates"]
        assert len({(round(c[0], 9), round(c[1], 9)) for c in coords}) >= 2


def test_strip_altitude_survives_the_roundtrip(runs: dict[str, PlanningRun]) -> None:
    """Высота съёмки доходит до выгрузки неизменной и одной на вылет."""
    run = runs["simple"]
    by_sortie = {sortie.id: sortie.survey_altitude_m for sortie in run.mission.sorties}
    for feature in _geojson(run)["features"]:
        properties = feature["properties"]
        if properties.get("phase") == "strip":
            assert properties["altitude_m"] == pytest.approx(
                by_sortie[properties["sortie_id"]], abs=0.1
            )


def test_kml_sets_altitude_mode_and_timestamps(
    tmp_path: Path, runs: dict[str, PlanningRun]
) -> None:
    """Умолчание KML — clampToGround: режим явно; метки времени — TimeSpan (№21)."""
    run = runs["demo_100km2"]
    path = export_mission(
        run.result, run.scenario, run.work_area.projector, tmp_path / "m.kml", ExportFormat.KML
    )
    root = ET.parse(path).getroot()
    modes = {element.text for element in root.iter(f"{_KML_NS}altitudeMode")}
    assert "relativeToGround" in modes
    assert list(root.iter(f"{_KML_NS}TimeSpan"))


def test_kml_of_infeasible_plan_lists_reasons(runs: dict[str, PlanningRun]) -> None:
    """Отчёт о невыполнимости в KML — папка причин и папка рекомендаций."""
    run = runs["nasty"]
    tree = mission_to_kml(run.result, run.scenario, run.work_area.projector)
    names = {element.text for element in tree.getroot().iter("name")}
    assert {"Причины невыполнимости", "Рекомендации"} <= names


def test_schedule_csv_has_a_row_per_sortie(run: PlanningRun) -> None:
    """№31: расписание отдельным CSV — строка на вылет."""
    rows = list(csv.DictReader(io.StringIO(schedule_to_csv(run.mission, run.scenario))))
    assert len(rows) == len(run.mission.sorties)


def test_format_is_picked_by_extension(tmp_path: Path, runs: dict[str, PlanningRun]) -> None:
    """`.kml` пишет KML, `.geojson` — GeoJSON, `.csv` — расписание."""
    run = runs["simple"]
    args = (run.result, run.scenario, run.work_area.projector)
    geojson = export_mission(*args, tmp_path / "m.geojson")
    kml = export_mission(*args, tmp_path / "m.kml")
    schedule = export_mission(*args, tmp_path / "m.csv")
    assert json.loads(geojson.read_text(encoding="utf-8"))["type"] == "FeatureCollection"
    assert kml.read_text(encoding="utf-8").lstrip().startswith("<?xml")
    assert schedule.read_text(encoding="utf-8").startswith("sortie_id,")
