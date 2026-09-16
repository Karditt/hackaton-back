"""Экспорт: вторая и последняя граница инварианта 1."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from core.catalog import VehicleCatalog
from core.export import export_mission, mission_to_geojson, mission_to_kml
from core.pipeline import run_pipeline
from core.schemas import ExportFormat, Scenario

_KML_NS = "{http://www.opengis.net/kml/2.2}"


def test_geojson_coordinates_are_wgs84(scenario: Scenario, catalog: VehicleCatalog) -> None:
    """Наружу уходят градусы, а не метры рабочей проекции."""
    run = run_pipeline(scenario, catalog=catalog)
    document = mission_to_geojson(run.mission, scenario, run.work_area.projector)

    assert document["type"] == "FeatureCollection"
    for feature in document["features"]:
        geometry = feature["geometry"]
        coords = (
            geometry["coordinates"]
            if geometry["type"] == "Point"
            else _flatten(geometry["coordinates"])
        )
        lon, lat = coords[0], coords[1]
        assert -180.0 <= lon <= 180.0
        assert -90.0 <= lat <= 90.0


def test_geojson_carries_metrics_and_stub_flags(
    simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Выгрузка несёт метрики, режим высоты и признак заглушек."""
    run = run_pipeline(simple_scenario, catalog=catalog)
    document = mission_to_geojson(run.mission, simple_scenario, run.work_area.projector)
    properties = document["properties"]

    assert properties["altitude_mode"] == "relativeToGround"
    assert properties["metrics"]["makespan_s"] > 0
    assert properties["stub_stages"], "план на заглушках обязан признаваться в этом"


def test_geojson_linestrings_have_two_distinct_points(
    scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """RFC 7946: LineString из двух совпадающих точек невалиден."""
    run = run_pipeline(scenario, catalog=catalog)
    document = mission_to_geojson(run.mission, scenario, run.work_area.projector)
    for feature in document["features"]:
        if feature["geometry"]["type"] != "LineString":
            continue
        coords = feature["geometry"]["coordinates"]
        assert len({(round(item[0], 9), round(item[1], 9)) for item in coords}) >= 2


def test_kml_sets_altitude_mode_explicitly(
    tmp_path: Path, simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Умолчание KML — clampToGround: режим обязан быть проставлен явно.

    Иначе план, рассчитанный на 250 м, откроется прижатым к земле и ошибка
    не будет видна глазом. Проверяется записанный файл, а не дерево в памяти:
    именно файл попадёт в стороннее ПО.
    """
    run = run_pipeline(simple_scenario, catalog=catalog)
    path = export_mission(
        run.mission,
        simple_scenario,
        run.work_area.projector,
        tmp_path / "mission.kml",
        ExportFormat.KML,
    )
    modes = {
        element.text
        for element in ET.parse(path).getroot().iter(f"{_KML_NS}altitudeMode")
        if element.text
    }
    assert "relativeToGround" in modes
    assert mission_to_kml(run.mission, simple_scenario, run.work_area.projector) is not None


def test_kml_is_well_formed_xml(
    tmp_path: Path, simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Записанный KML должен читаться сторонним разбором без правок."""
    run = run_pipeline(simple_scenario, catalog=catalog)
    path = export_mission(
        run.mission,
        simple_scenario,
        run.work_area.projector,
        tmp_path / "mission.kml",
        ExportFormat.KML,
    )
    root = ET.parse(path).getroot()
    assert root.tag == f"{_KML_NS}kml"
    assert list(root.iter(f"{_KML_NS}Placemark"))


def test_format_is_picked_by_extension(
    tmp_path: Path, simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """`-o mission.kml` пишет KML, `-o mission.geojson` — GeoJSON."""
    run = run_pipeline(simple_scenario, catalog=catalog)
    geojson_path = export_mission(
        run.mission, simple_scenario, run.work_area.projector, tmp_path / "m.geojson"
    )
    kml_path = export_mission(
        run.mission, simple_scenario, run.work_area.projector, tmp_path / "m.kml"
    )
    assert json.loads(geojson_path.read_text(encoding="utf-8"))["type"] == "FeatureCollection"
    assert kml_path.read_text(encoding="utf-8").lstrip().startswith("<?xml")


def test_altitudes_survive_the_roundtrip(
    simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Высота галса доходит до выгрузки неизменной."""
    run = run_pipeline(simple_scenario, catalog=catalog)
    document = mission_to_geojson(run.mission, simple_scenario, run.work_area.projector)
    strip_altitudes = {
        feature["properties"]["altitude_m"]
        for feature in document["features"]
        if feature["properties"].get("phase") == "strip"
    }
    expected = run.result.survey_plan.reference().altitude_agl_m
    assert strip_altitudes
    assert all(altitude == pytest.approx(expected, abs=0.1) for altitude in strip_altitudes)


def _flatten(coordinates) -> list[float]:
    """Достать первую координатную пару из вложенной структуры GeoJSON."""
    item = coordinates
    while isinstance(item[0], list):
        item = item[0]
    return item
