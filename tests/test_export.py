"""Экспорт: вторая граница инварианта 1 и формат из docs/export_format.md (№21)."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from core.catalog import VehicleCatalog
from core.cli import load_scenario
from core.errors import ExportError
from core.export import (
    KeyPointKind,
    boards_of,
    export_mission,
    kml_to_bytes,
    mission_files,
    mission_to_geojson,
    mission_to_kml,
    mission_zip,
    scenario_to_geojson,
    schedule_to_csv,
    sortie_keypoints,
)
from core.export.common import oriented_rings, relative_time, span_label, time_label, utc_time
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import ExportFormat, FlightPhase
from tests.conftest import SCENARIOS_DIR

_KML_NS = "{http://www.opengis.net/kml/2.2}"
_GX_NS = "{http://www.google.com/kml/ext/2.2}"
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _geojson(run: PlanningRun, board: str | None = None) -> dict:
    return mission_to_geojson(run.result, run.scenario, run.work_area.projector, board)


def _kml_root(run: PlanningRun, board: str | None = None) -> ET.Element:
    """KML так, как его прочтёт сторонняя программа: из байтов, с пространствами имён."""
    tree = mission_to_kml(run.result, run.scenario, run.work_area.projector, board)
    return ET.fromstring(kml_to_bytes(tree))


def _k(tag: str) -> str:
    return f"{_KML_NS}{tag}"


def _data(element: ET.Element) -> dict[str, str]:
    """ExtendedData элемента KML как словарь."""
    extended = element.find(_k("ExtendedData"))
    if extended is None:
        return {}
    return {
        item.get("name", ""): item.findtext(_k("value"), "") for item in extended.iter(_k("Data"))
    }


def _first_coordinate(coordinates) -> list[float]:
    item = coordinates
    while isinstance(item[0], list):
        item = item[0]
    return item


# ---------------------------------------------------------------- GeoJSON


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
    assert properties["time_reference"] == "utc"
    assert properties["stub_stages"] == [stage.value for stage in run.mission.stub_stages]


def test_feasible_export_has_routes_cells_keypoints_and_schedule(
    runs: dict[str, PlanningRun],
) -> None:
    """№21: линии маршрута, полигоны подобластей, ключевые точки; метки времени; расписание."""
    run = runs["demo_100km2"]
    document = _geojson(run)
    kinds = {feature["properties"].get("kind") for feature in document["features"]}
    assert {"leg", "keypoint", "survey_cell", "maneuver_zone", "obstacle", "no_fly_zone"} <= kinds
    legs = [f for f in document["features"] if f["properties"]["kind"] == "leg"]
    assert all(_UTC_RE.match(f["properties"]["start_time"]) for f in legs), (
        "начало работ задано — время абсолютное, UTC, до секунды"
    )
    assert len(document["properties"]["schedule"]) == len(run.mission.sorties)
    assert [item["board_id"] for item in document["properties"]["boards"]] == [
        board.id for board in boards_of(run.mission)
    ]


def test_infeasible_export_is_a_report_without_routes(runs: dict[str, PlanningRun]) -> None:
    """Инвариант 10: у невыполнимого плана в выгрузке нет маршрутов — есть причины и советы."""
    document = _geojson(runs["nasty"])
    kinds = {feature["properties"].get("kind") for feature in document["features"]}
    assert not kinds & {"leg", "keypoint", "survey_cell"}
    assert document["properties"]["status"] == "infeasible"
    assert document["properties"]["reasons"]
    assert document["properties"]["recommendations"]
    assert document["properties"]["boards"] == []


def test_scenario_layers_are_the_plan_context(runs: dict[str, PlanningRun]) -> None:
    """Слои сценария до расчёта — те же признаки, что слои сценария в выгрузке плана.

    Интерфейс показывает их до расчёта и прячет по видам одним кодом (E10).
    """
    run = runs["demo_100km2"]
    layers = scenario_to_geojson(run.scenario)
    context = {"survey_area", "no_fly_zone", "airspace_boundary", "obstacle", "site"}
    in_plan = [f for f in _geojson(run)["features"] if f["properties"]["kind"] in context]
    assert layers["features"] == in_plan

    properties = layers["properties"]
    assert properties["scenario_id"] == run.scenario.id
    assert properties["counts"]["obstacle"] == len(run.scenario.obstacles)
    assert properties["counts"]["no_fly_zone"] == len(run.scenario.no_fly_zones)
    assert properties["counts"]["site"] == len(run.scenario.sites)
    assert properties["area_km2"] == pytest.approx(run.work_area.area.area / 1e6, rel=1e-3)


def test_geojson_linestrings_have_two_distinct_points(run: PlanningRun) -> None:
    """RFC 7946: LineString из двух совпадающих точек невалиден."""
    for feature in _geojson(run)["features"]:
        if feature["geometry"]["type"] != "LineString":
            continue
        coords = feature["geometry"]["coordinates"]
        assert len({(round(c[0], 9), round(c[1], 9)) for c in coords}) >= 2


def _twice_area(ring: list[list[float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:], strict=False))


def test_geojson_rings_follow_the_right_hand_rule(run: PlanningRun) -> None:
    """RFC 7946 §3.1.6: внешнее кольцо — против часовой стрелки, отверстия — по часовой."""
    checked = 0
    for feature in _geojson(run)["features"]:
        geometry = feature["geometry"]
        if geometry["type"] == "Polygon":
            polygons = [geometry["coordinates"]]
        elif geometry["type"] == "MultiPolygon":
            polygons = geometry["coordinates"]
        else:
            continue
        for rings in polygons:
            for index, ring in enumerate(rings):
                assert ring[0] == ring[-1], "кольцо замкнуто"
                assert (_twice_area(ring) > 0) == (index == 0)
                checked += 1
    assert checked


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


# ---------------------------------------------------------------- ключевые точки


def test_keypoints_bound_the_flight_stages(runs: dict[str, PlanningRun]) -> None:
    """Взлёт первым, посадка последней, между ними — съёмка каждой подобласти.

    Время взлёта и посадки совпадает с расписанием: файлы не расходятся.
    """
    run = runs["demo_100km2"]
    schedule = {entry.sortie_id: entry for entry in run.mission.schedule.entries}  # type: ignore[union-attr]
    for sortie in run.mission.sorties:
        points = sortie_keypoints(sortie)
        assert points[0].kind is KeyPointKind.TAKEOFF
        assert points[-1].kind is KeyPointKind.LANDING
        times = [point.time_s for point in points]
        assert times == sorted(times)
        assert points[0].time_s == pytest.approx(schedule[sortie.id].start_s)
        assert points[-1].time_s == pytest.approx(schedule[sortie.id].end_s)
        site = run.work_area.sites[sortie.takeoff_site_id]
        assert math.hypot(points[0].x_m - site.x_m, points[0].y_m - site.y_m) < 1.0
        surveyed = {leg.cell_id for leg in sortie.legs if leg.phase is FlightPhase.STRIP}
        starts = [point for point in points if point.kind is KeyPointKind.SURVEY_START]
        ends = [point for point in points if point.kind is KeyPointKind.SURVEY_END]
        assert {point.cell_id for point in starts} == surveyed == {p.cell_id for p in ends}
        for point in starts + ends:
            assert point.alt_m == pytest.approx(sortie.survey_altitude_m, abs=0.1)


# ---------------------------------------------------------------- KML: формат

#: Последовательности дочерних элементов по схеме OGC ogckml22.xsd и kml22gx.xsd
#: для элементов, которые пишет выгрузка. Слот — набор тегов на одном месте
#: последовательности и признак «может повторяться».
_FEATURE = [
    ({"name"}, False),
    ({"visibility"}, False),
    ({"open"}, False),
    ({"Snippet"}, False),
    ({"description"}, False),
    ({"TimeStamp", "TimeSpan"}, False),
    ({"styleUrl"}, False),
    ({"Style", "StyleMap"}, True),
    ({"ExtendedData"}, False),
]
_FEATURES = ({"Placemark", "Folder", "Document"}, True)
_GEOMETRY = ({"Point", "LineString", "Polygon", "gx:Track"}, False)
_SEQUENCES: dict[str, list[tuple[set[str], bool]]] = {
    "kml": [({"Document"}, False)],
    "Document": [*_FEATURE, ({"Schema"}, True), _FEATURES],
    "Folder": [*_FEATURE, _FEATURES],
    "Placemark": [*_FEATURE, _GEOMETRY],
    "Style": [
        ({"IconStyle"}, False),
        ({"LabelStyle"}, False),
        ({"LineStyle"}, False),
        ({"PolyStyle"}, False),
    ],
    "StyleMap": [({"Pair"}, True)],
    "Pair": [({"key"}, False), ({"styleUrl"}, False)],
    "IconStyle": [({"color"}, False), ({"scale"}, False)],
    "LabelStyle": [({"color"}, False), ({"scale"}, False)],
    "LineStyle": [({"color"}, False), ({"width"}, False)],
    "PolyStyle": [({"color"}, False), ({"fill"}, False), ({"outline"}, False)],
    "Point": [({"extrude"}, False), ({"altitudeMode"}, False), ({"coordinates"}, False)],
    "LineString": [
        ({"extrude"}, False),
        ({"tessellate"}, False),
        ({"altitudeMode"}, False),
        ({"coordinates"}, False),
    ],
    "Polygon": [
        ({"extrude"}, False),
        ({"tessellate"}, False),
        ({"altitudeMode"}, False),
        ({"outerBoundaryIs"}, False),
        ({"innerBoundaryIs"}, True),
    ],
    "outerBoundaryIs": [({"LinearRing"}, False)],
    "innerBoundaryIs": [({"LinearRing"}, False)],
    "LinearRing": [({"coordinates"}, False)],
    "TimeSpan": [({"begin"}, False), ({"end"}, False)],
    "TimeStamp": [({"when"}, False)],
    "ExtendedData": [({"Data"}, True)],
    "Data": [({"displayName"}, False), ({"value"}, False)],
    "gx:Track": [({"altitudeMode"}, False), ({"when"}, True), ({"gx:coord"}, True)],
}


def _local(tag: str) -> str:
    """Тег без пространства имён; расширение Google — с префиксом `gx:`."""
    if tag.startswith(_GX_NS):
        return "gx:" + tag[len(_GX_NS) :]
    return tag.removeprefix(_KML_NS)


def _order_violations(root: ET.Element) -> list[str]:
    """Нарушения порядка и состава дочерних элементов относительно схемы."""
    problems: list[str] = []
    for element in root.iter():
        sequence = _SEQUENCES.get(_local(element.tag))
        if sequence is None:
            continue
        position = 0
        used: set[int] = set()
        for child in element:
            tag = _local(child.tag)
            slot = next(
                (index for index in range(position, len(sequence)) if tag in sequence[index][0]),
                None,
            )
            if slot is None:
                problems.append(f"<{tag}> не на месте внутри <{_local(element.tag)}>")
                break
            if slot in used and not sequence[slot][1]:
                problems.append(f"<{tag}> повторяется внутри <{_local(element.tag)}>")
                break
            used.add(slot)
            position = slot
    return problems


@pytest.mark.parametrize("name", ["simple", "demo_100km2", "nasty"])
def test_kml_follows_the_ogc_schema_order(runs: dict[str, PlanningRun], name: str) -> None:
    """Порядок элементов — последовательность схемы OGC KML 2.2, а не произвольный набор.

    Нарушенный порядок строгие читатели KML отвергают, нестрогие молча теряют
    элементы — проверяется и обзор плана, и задание борта, и отчёт.
    """
    run = runs[name]
    roots = [_kml_root(run)] + [_kml_root(run, board.id) for board in boards_of(run.mission)[:2]]
    for root in roots:
        assert _order_violations(root) == []


def test_kml_sets_altitude_mode_and_timestamps(
    tmp_path: Path, runs: dict[str, PlanningRun]
) -> None:
    """Умолчание KML — clampToGround: режим явно; метки времени — TimeSpan и TimeStamp (№21)."""
    run = runs["demo_100km2"]
    path = export_mission(
        run.result, run.scenario, run.work_area.projector, tmp_path / "m.kml", ExportFormat.KML
    )
    root = ET.parse(path).getroot()
    modes = {element.text for element in root.iter(_k("altitudeMode"))}
    assert "relativeToGround" in modes
    for geometry in ("Point", "LineString", "Polygon"):
        for element in root.iter(_k(geometry)):
            assert element.find(_k("altitudeMode")) is not None, geometry
    assert list(root.iter(_k("TimeSpan")))
    assert list(root.iter(_k("TimeStamp")))


def test_kml_times_are_whole_second_utc(runs: dict[str, PlanningRun]) -> None:
    """dateTime XML Schema в UTC с «Z», без долей секунды — это читают все просмотрщики."""
    root = _kml_root(runs["demo_100km2"])
    moments = [
        element.text for tag in (_k("when"), _k("begin"), _k("end")) for element in root.iter(tag)
    ]
    assert moments
    assert all(_UTC_RE.match(moment or "") for moment in moments)


def test_kml_colors_are_aabbggrr(runs: dict[str, PlanningRun]) -> None:
    """Цвет KML — aabbggrr: красный галс должен кончаться красным каналом, а не начинаться.

    Раньше цвета фаз были записаны как rrggbb, и в Google Earth галсы выходили
    синими, а посадка — красной, наоборот карте веба.
    """
    root = _kml_root(runs["simple"])
    colors = {
        style.get("id"): style.findtext(f"{_k('LineStyle')}/{_k('color')}")
        for style in root.iter(_k("Style"))
    }
    assert colors["phase-strip"] == "f22626dc"  # веб: #dc2626
    assert colors["phase-landing"] == "f2d84e1d"  # веб: #1d4ed8
    assert colors["phase-climb"] == "f2e9a50e"  # веб: #0ea5e9


def test_kml_track_replays_each_sortie(runs: dict[str, PlanningRun]) -> None:
    """gx:Track: столько же `when`, сколько `gx:coord`; время растёт от взлёта до посадки."""
    run = runs["medium"]
    rows = {
        row["sortie_id"]: row
        for row in csv.DictReader(io.StringIO(schedule_to_csv(run.mission, run.scenario)))
    }
    tracks = 0
    for placemark in _kml_root(run).iter(_k("Placemark")):
        track = placemark.find(f"{_GX_NS}Track")
        if track is None:
            continue
        tracks += 1
        whens = [element.text or "" for element in track.findall(_k("when"))]
        coords = [element.text or "" for element in track.findall(f"{_GX_NS}coord")]
        assert len(whens) == len(coords) >= 2
        assert whens == sorted(set(whens)), "моменты строго растут"
        assert all(len(coord.split(" ")) == 3 and "," not in coord for coord in coords)
        row = rows[_data(placemark)["sortie_id"]]
        assert (whens[0], whens[-1]) == (row["start_time_utc"], row["end_time_utc"])
    assert tracks == len(run.mission.sorties)


def test_kml_schedule_matches_csv(runs: dict[str, PlanningRun]) -> None:
    """№31, №33: расписание в KML и в CSV — одни и те же строки."""
    run = runs["demo_100km2"]
    rows = list(csv.DictReader(io.StringIO(schedule_to_csv(run.mission, run.scenario))))
    root = _kml_root(run)
    folder = next(f for f in root.iter(_k("Folder")) if f.findtext(_k("name")) == "Расписание")
    entries = [_data(placemark) for placemark in folder.findall(_k("Placemark"))]
    assert [entry.pop("kind") for entry in entries] == ["schedule_entry"] * len(rows)
    assert entries == [{key: value for key, value in row.items() if value} for row in rows]


def test_kml_of_infeasible_plan_lists_reasons(runs: dict[str, PlanningRun]) -> None:
    """Отчёт о невыполнимости в KML — папка причин и папка рекомендаций."""
    run = runs["nasty"]
    tree = mission_to_kml(run.result, run.scenario, run.work_area.projector)
    names = {element.text for element in tree.getroot().iter("name")}
    assert {"Причины невыполнимости", "Рекомендации"} <= names
    assert "Полётные задания" not in names


# ---------------------------------------------------------------- задания бортов


def test_board_files_split_the_plan_without_overlap(runs: dict[str, PlanningRun]) -> None:
    """ТЗ: задание на каждый борт. Каждый вылет — ровно в одном задании, своего борта."""
    run = runs["demo_100km2"]
    seen: list[str] = []
    for board in boards_of(run.mission):
        root = _kml_root(run, board.id)
        folders = [f for f in root.iter(_k("Folder")) if _data(f).get("kind") == "sortie"]
        assert folders
        assert {_data(folder)["board_id"] for folder in folders} == {board.id}
        seen.extend(_data(folder)["sortie_id"] for folder in folders)
        schedule = [
            p for p in root.iter(_k("Placemark")) if _data(p).get("kind") == "schedule_entry"
        ]
        assert len(schedule) == len(folders)
    assert sorted(seen) == sorted(sortie.id for sortie in run.mission.sorties)


def test_board_geojson_and_csv_are_filtered(runs: dict[str, PlanningRun]) -> None:
    """GeoJSON и расписание одного борта — только его вылеты и подобласти, слои — целиком."""
    run = runs["medium"]
    board = boards_of(run.mission)[0]
    document = _geojson(run, board.id)
    boards = {
        f["properties"]["board_id"]
        for f in document["features"]
        if f["properties"]["kind"] in ("leg", "keypoint", "survey_cell", "maneuver_zone")
    }
    assert boards == {board.id}
    assert any(f["properties"]["kind"] == "no_fly_zone" for f in document["features"])
    rows = list(csv.DictReader(io.StringIO(schedule_to_csv(run.mission, run.scenario, board.id))))
    assert {row["board_id"] for row in rows} == {board.id}
    assert len(rows) == len(board.sorties)


def test_unknown_board_is_an_error(runs: dict[str, PlanningRun]) -> None:
    """Неизвестный борт — понятная ошибка со списком бортов, а не пустой файл."""
    run = runs["simple"]
    with pytest.raises(ExportError, match="geoscan-gemini_1"):
        mission_to_kml(run.result, run.scenario, run.work_area.projector, "no-such_1")
    with pytest.raises(ExportError, match="невыполним"):
        _geojson(runs["nasty"], "geoscan-gemini_1")


def test_package_holds_overview_and_board_files(runs: dict[str, PlanningRun]) -> None:
    """Комплект: обзор, расписание и задание каждого борта; архив воспроизводим побитово."""
    run = runs["simple"]
    args = (run.result, run.scenario, run.work_area.projector)
    expected = {"simple.geojson", "simple.kml", "simple.schedule.csv"} | {
        f"simple.{board.id}.kml" for board in boards_of(run.mission)
    }
    assert set(mission_files(*args)) == expected
    archive = mission_zip(*args)
    assert archive == mission_zip(*args)
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        assert set(opened.namelist()) == expected

    report = runs["nasty"]
    assert set(mission_files(report.result, report.scenario, report.work_area.projector)) == {
        "nasty.geojson",
        "nasty.kml",
        "nasty.schedule.csv",
    }


# ---------------------------------------------------------------- время


def test_without_start_time_the_time_is_relative(catalog: VehicleCatalog) -> None:
    """Долг №30: без начала работ нет ни TimeSpan, ни трека — и это сказано явно.

    «Сейчас» не подставляется: один сценарий давал бы разные файлы (docs/dev/AGENT.md §8).
    """
    scenario = load_scenario(SCENARIOS_DIR / "simple.json").model_copy(update={"start_time": None})
    run = run_pipeline(scenario, catalog=catalog)
    root = _kml_root(run)
    for tag in (_k("TimeSpan"), _k("TimeStamp"), f"{_GX_NS}Track"):
        assert not list(root.iter(tag))
    assert "Время относительное" in (root.find(_k("Document")).findtext(_k("description")) or "")
    keypoints = [p for p in root.iter(_k("Placemark")) if _data(p).get("kind") == "keypoint"]
    assert keypoints and all("T+" in (p.findtext(_k("name")) or "") for p in keypoints)
    assert _geojson(run)["properties"]["time_reference"] == "relative"
    rows = list(csv.DictReader(io.StringIO(schedule_to_csv(run.mission, run.scenario))))
    assert rows and all(row["start_time_utc"] == "" for row in rows)


def test_time_formatting() -> None:
    """UTC — до целой секунды и с «Z»; пояс переводится в UTC; относительное — T+чч:мм:сс."""
    start = datetime.fromisoformat("2026-09-20T09:00:00+03:00")
    assert utc_time(start, 0.0) == "2026-09-20T06:00:00Z"
    assert utc_time(start, 61.6) == "2026-09-20T06:01:02Z"
    assert utc_time(datetime.fromisoformat("2026-09-20T06:00:00"), 0.0) == "2026-09-20T06:00:00Z"
    assert utc_time(None, 10.0) is None
    assert relative_time(3725.4) == "T+01:02:05"
    assert time_label(start, 3600.0) == "07:00:00 UTC"
    assert time_label(start, 20 * 3600.0) == "21.09 02:00:00 UTC", "другой день — с датой"
    assert span_label(start, 0.0, 60.0) == "06:00:00–06:01:00 UTC"
    assert span_label(None, 0.0, 60.0) == "T+00:00:00–T+00:01:00"


def test_rings_are_oriented_and_closed() -> None:
    """Внешнее кольцо по часовой — развернётся; отверстие против часовой — тоже."""
    clockwise = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    counter = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8), (0.2, 0.2)]
    outer, hole = oriented_rings([clockwise, counter])
    assert outer[0] == outer[-1] and hole[0] == hole[-1]
    assert _twice_area([list(p) for p in outer]) > 0
    assert _twice_area([list(p) for p in hole]) < 0


# ---------------------------------------------------------------- прочее


def test_schedule_csv_has_a_row_per_sortie(run: PlanningRun) -> None:
    """№31: расписание отдельным CSV — строка на вылет, с бортом."""
    rows = list(csv.DictReader(io.StringIO(schedule_to_csv(run.mission, run.scenario))))
    assert len(rows) == len(run.mission.sorties)
    assert all(row["board_id"] for row in rows)


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
