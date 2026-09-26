"""Чтение входных слоёв KML (E1) — на настоящих файлах заказчика.

Числа в проверках не выдуманы: они измерены по образцам и записаны в
[`data/samples/README.md`](../data/samples/README.md). Если импорт изменится и
числа поедут — расходиться должны и README, и тесты, осознанно.
"""

from __future__ import annotations

import json

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from core.errors import GeometryError
from core.geo.crs import pick_working_crs
from core.geo.kml_import import (
    ImportedLayers,
    import_layers,
    parse_altitude_text,
    read_area,
    read_obstacles,
    read_zones,
)
from core.geo.project import Projector
from core.pipeline import check_scenario
from core.schemas import GeoMultiPolygon, ImportReport, NoFlyZone, Obstacle, Scenario
from tests.conftest import REPO_ROOT

SAMPLES = REPO_ROOT / "data" / "samples"
AREA_KML = SAMPLES / "survey_area_msk.kml"
ZONES_KML = SAMPLES / "zones_msk.kml"
OBSTACLES_KML = SAMPLES / "obstacles_msk.kml"
OBSTACLES_PRIMORYE_KML = SAMPLES / "obstacles_primorye.kml"


@pytest.fixture(scope="module")
def area() -> tuple[GeoMultiPolygon, ImportReport]:
    """Область съёмки заказчика читается один раз на модуль."""
    return read_area(AREA_KML)


@pytest.fixture(scope="module")
def layers() -> ImportedLayers:
    """Все три слоя заказчика вместе."""
    return import_layers(AREA_KML, ZONES_KML, OBSTACLES_KML)


def _codes(report: ImportReport) -> set[str]:
    return {warning.code for warning in report.warnings}


def _metric_parts(multipolygon: GeoMultiPolygon) -> list[Polygon]:
    """Части области в рабочей метрической проекции — как их видит ядро."""
    crs, _ = pick_working_crs(
        [point for polygon in multipolygon.polygons for point in polygon.outer]
    )
    projector = Projector(crs)
    return [
        Polygon(
            projector.ring_to_metric(polygon.outer),
            [projector.ring_to_metric(hole) for hole in polygon.holes],
        )
        for polygon in multipolygon.polygons
    ]


# --------------------------------------------------------------------------
# Область съёмки
# --------------------------------------------------------------------------


def test_area_counts_match_the_measured_sample(area: tuple[GeoMultiPolygon, ImportReport]) -> None:
    """Файл области читается целиком, с теми же числами, что в README образцов."""
    multipolygon, report = area
    assert report.area.read == 696
    assert report.area.fixed == 5
    assert report.area.dropped == 135
    # 561 уцелевшая запись даёт 549 раздельных кусков: у остальных общая граница.
    assert report.area.kept == 549
    assert report.area.kept == len(multipolygon.polygons)
    assert report.area_km2 == pytest.approx(245.0, abs=0.5)


def test_area_reports_what_it_fixed_and_dropped(area: tuple[GeoMultiPolygon, ImportReport]) -> None:
    """Починка, слияние и выбрасывание не делаются молча (docs/input_formats.md §1)."""
    assert {
        "import.geometry_fixed",
        "import.geometry_dropped",
        "import.geometry_merged",
    } <= _codes(area[1])


def test_area_is_a_valid_multipolygon(area: tuple[GeoMultiPolygon, ImportReport]) -> None:
    """Куски с общей границей слиты: иначе мультиполигон некорректен по OGC.

    Без слияния `build_work_area` отвергает собранный сценарий с сообщением о
    самопересечении — так и случилось на первом прогоне по файлам заказчика.
    """
    assert MultiPolygon(_metric_parts(area[0])).is_valid


def test_assembled_scenario_is_accepted_by_core(layers: ImportedLayers) -> None:
    """Сценарий, собранный из слоёв заказчика, проходит приём ядра.

    Это главная проверка импорта: не «файл разобрался», а «получившимся
    сценарием можно планировать». Параметры берутся из того же файла, которым
    сценарий собирает CLI.
    """
    params_path = REPO_ROOT / "scenarios" / "sources" / "real_msk" / "params.json"
    payload = json.loads(params_path.read_text("utf-8"))
    payload["area"] = layers.area.model_dump(mode="json")
    payload["no_fly_zones"] = [zone.model_dump(mode="json") for zone in layers.zones]
    payload["obstacles"] = [obstacle.model_dump(mode="json") for obstacle in layers.obstacles]
    scenario = Scenario.model_validate(payload)
    check_scenario(scenario)


def test_area_pieces_are_valid_geometry(area: tuple[GeoMultiPolygon, ImportReport]) -> None:
    """Ни один принятый кусок не приносит в сценарий некорректную геометрию.

    Проверяется в метрах, а не в градусах: все геометрические решения ядро
    принимает в рабочей проекции (инвариант 1), а перевод в градусы на границе
    округляет координаты и может «слепить» соседние вершины у самых узких
    кусков — для расчёта это безразлично.
    """
    multipolygon, _ = area
    for part in _metric_parts(multipolygon):
        assert part.is_valid
        assert not part.is_empty


def test_area_keeps_holes(area: tuple[GeoMultiPolygon, ImportReport]) -> None:
    """Отверстия кусков — это вырезы в задании, терять их нельзя (заказчик, №9)."""
    multipolygon, _ = area
    assert sum(len(polygon.holes) for polygon in multipolygon.polygons) > 0


def test_area_without_polygons_is_refused() -> None:
    """Файл без единого полигона — ошибка входа, а не пустая область."""
    with pytest.raises(GeometryError, match="ни одного полигона"):
        read_area("<kml><Document><Placemark><name>пусто</name></Placemark></Document></kml>")


# --------------------------------------------------------------------------
# Высоты зон: разбор текста заказчика
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "low", "high", "datum"),
    [
        ("На всех высотах", None, None, ""),
        ("От земли до 450 м (1500 фут) AGL", 0.0, 450.0, "AGL"),
        ("От земли до 500 м (1700 фут) AMSL", 0.0, 500.0, "AMSL"),
        ("От земли до FL140", 0.0, 4267.2, "AMSL"),
        ("От 1200 м (4000 фут) AMSL до FL240", 1200.0, 7315.2, "AMSL"),
        ("От FL280 до FL400", 8534.4, 12192.0, "AMSL"),
        # Префикс поставщика данных не должен мешать разбору высот.
        ("Некорректная геометрия! От 800 м (2700 фут) AMSL до FL90", 800.0, 2743.2, "AMSL"),
        # Латинская «y» в слове «фyт» — опечатка распознавания в файле заказчика.
        ("От земли до 200 м (700 фyт) AGL\n\nНе распространяется на...", 0.0, 200.0, "AGL"),
    ],
)
def test_zone_altitudes_are_parsed(
    text: str, low: float | None, high: float | None, datum: str
) -> None:
    """Шаблоны высот из файла заказчика (docs/input_formats.md §3.1)."""
    parsed = parse_altitude_text(text)
    assert parsed.parsed
    assert parsed.datum == datum
    if low is None:
        assert parsed.min_m is None and parsed.max_m is None
    else:
        assert parsed.min_m == pytest.approx(low)
        assert parsed.max_m == pytest.approx(high)


@pytest.mark.parametrize("text", ["SVO", "", "какой-то текст без высот", "От земли"])
def test_unparsed_altitudes_do_not_become_zero(text: str) -> None:
    """Неразобранный текст — не «зона от нуля до нуля», а честное «не разобрано»."""
    parsed = parse_altitude_text(text)
    assert not parsed.parsed
    assert parsed.min_m is None and parsed.max_m is None


# --------------------------------------------------------------------------
# Зоны
# --------------------------------------------------------------------------


def test_zones_are_selected_by_area(layers: ImportedLayers) -> None:
    """Файл зон покрывает пол-страны, берутся только задевающие область."""
    report = layers.report
    assert report.zones.read == 341
    assert report.zones.kept == 10
    assert report.zones.skipped == 331
    assert len(layers.zones) == len(set(zone.id for zone in layers.zones))


def test_zone_altitudes_reach_the_contract(layers: ImportedLayers) -> None:
    """Высоты зоны доезжают до контракта в метрах над поверхностью."""
    by_id = {zone.id: zone for zone in layers.zones}
    assert by_id["UUP62"].min_alt_m == pytest.approx(0.0)
    assert by_id["UUP62"].max_alt_m == pytest.approx(450.0)


def test_temporary_zones_without_window_are_reported(layers: ImportedLayers) -> None:
    """У временных зон в файле нет времени действия — об этом говорится вслух."""
    assert "import.zone_window_unknown" in _codes(layers.report)
    assert all(zone.is_permanent() for zone in layers.zones)


def test_proposed_machine_format_wins_over_text() -> None:
    """Предложенный формат (§3.2) читается и сильнее свободного текста."""
    kml = """
    <kml><Document><Placemark>
      <name>UUR201</name>
      <description>От земли до 450 м (1500 фут) AGL</description>
      <ExtendedData>
        <Data name="zone_id"><value>UUR201</value></Data>
        <Data name="zone_kind"><value>restricted</value></Data>
        <Data name="alt_datum"><value>AGL</value></Data>
        <Data name="alt_min_m"><value>100</value></Data>
        <Data name="alt_max_m"><value>300</value></Data>
      </ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        37.0,55.0 37.1,55.0 37.1,55.1 37.0,55.1 37.0,55.0
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark></Document></kml>
    """
    zones, _ = read_zones(kml)
    assert len(zones) == 1
    assert zones[0].id == "UUR201"
    assert zones[0].min_alt_m == pytest.approx(100.0)
    assert zones[0].max_alt_m == pytest.approx(300.0)


def test_amsl_zone_is_lowered_by_terrain() -> None:
    """Отметки над уровнем моря опускаются на рельеф — зона расширяется вниз."""
    kml = """
    <kml><Document><Placemark>
      <name>Z</name><description>От 800 м (2700 фут) AMSL до FL90</description>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        37.0,55.0 37.1,55.0 37.1,55.1 37.0,55.1 37.0,55.0
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark></Document></kml>
    """
    zones, _ = read_zones(kml, terrain_min_m=100.0, terrain_max_m=200.0)
    assert zones[0].min_alt_m == pytest.approx(600.0)
    assert zones[0].max_alt_m == pytest.approx(2743.2 - 100.0)


# --------------------------------------------------------------------------
# Препятствия
# --------------------------------------------------------------------------


def test_obstacles_counts_match_the_measured_sample() -> None:
    """Оба файла препятствий читаются целиком, кроме битых записей."""
    moscow, moscow_report = read_obstacles(OBSTACLES_KML)
    primorye, primorye_report = read_obstacles(OBSTACLES_PRIMORYE_KML)
    assert moscow_report.obstacles.read == 5164
    assert moscow_report.obstacles.kept == 5162
    assert moscow_report.obstacles.dropped == 2
    assert primorye_report.obstacles.read == 3201
    assert primorye_report.obstacles.kept == 3201
    assert len(moscow) == 5162
    assert len(primorye) == 3201


def test_nonstandard_altitude_mode_is_accepted_and_reported() -> None:
    """`relativeToSeaFloor` — не значение KML: принимаем как AMSL и говорим об этом."""
    moscow, report = read_obstacles(OBSTACLES_KML)
    assert "import.obstacle_altitude_mode_nonstandard" in _codes(report)
    assert max(obstacle.height_m for obstacle in moscow) == pytest.approx(403.8, abs=0.1)


def test_obstacle_datum_decides_what_z_means() -> None:
    """При `absolute` высота меряется от рельефа, при `relativeToGround` — как есть."""
    template = """
    <kml><Document><Placemark>
      <name>OBJ TOWER</name>
      <Polygon><extrude>1</extrude><altitudeMode>{mode}</altitudeMode>
        <outerBoundaryIs><LinearRing><coordinates>
          37.000,55.000,{z} 37.001,55.000,{z} 37.001,55.001,{z} 37.000,55.001,{z} 37.000,55.000,{z}
        </coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
    </Placemark></Document></kml>
    """
    agl, _ = read_obstacles(template.format(mode="relativeToGround", z=200.0), terrain_m=150.0)
    amsl, _ = read_obstacles(template.format(mode="absolute", z=200.0), terrain_m=150.0)
    assert agl[0].height_m == pytest.approx(200.0)
    assert amsl[0].height_m == pytest.approx(50.0)
    assert agl[0].id == "OBJ"
    assert agl[0].name == "TOWER"


def test_linear_obstacle_becomes_a_strip() -> None:
    """ЛЭП — LineString: контракт знает только полигон, линия разворачивается в полосу."""
    kml = """
    <kml><Document><Placemark>
      <name>LINE POWER</name>
      <LineString><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode>
        <coordinates>37.000,55.000,60 37.010,55.000,60</coordinates>
      </LineString>
    </Placemark></Document></kml>
    """
    obstacles, report = read_obstacles(kml)
    assert len(obstacles) == 1
    assert obstacles[0].polygon is not None
    assert obstacles[0].height_m == pytest.approx(60.0)
    assert "import.obstacle_line_buffered" in _codes(report)


def test_degenerate_obstacle_records_are_dropped() -> None:
    """Запись с координатами 0,0 — мусор выгрузки, а не препятствие в Гвинейском заливе."""
    kml = """
    <kml><Document><Placemark>
      <LineString><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode>
        <coordinates>0.0, 0.0, 0.0</coordinates>
      </LineString>
    </Placemark></Document></kml>
    """
    obstacles, report = read_obstacles(kml)
    assert obstacles == []
    assert report.obstacles.dropped == 1
    assert "import.obstacle_dropped" in _codes(report)


# --------------------------------------------------------------------------
# Три слоя вместе
# --------------------------------------------------------------------------


def test_layers_are_cut_to_the_area(layers: ImportedLayers) -> None:
    """Зоны и препятствия отбираются по области: в сценарий не едет вся страна."""
    report = layers.report
    assert report.obstacles.read == 5164
    assert report.obstacles.kept == 547
    assert report.obstacles.skipped == 4615
    lons = [point.lon for polygon in layers.area.polygons for point in polygon.outer]
    lats = [point.lat for polygon in layers.area.polygons for point in polygon.outer]
    frame = box(min(lons) - 0.1, min(lats) - 0.1, max(lons) + 0.1, max(lats) + 0.1)
    for obstacle in layers.obstacles:
        assert obstacle.polygon is not None
        point = obstacle.polygon.outer[0]
        assert frame.covers(Polygon([(point.lon, point.lat)] * 3).centroid)


def test_layers_fit_the_contract(layers: ImportedLayers) -> None:
    """Импорт отдаёт ровно те типы, из которых собирается сценарий."""
    assert isinstance(layers.area, GeoMultiPolygon)
    assert all(isinstance(zone, NoFlyZone) for zone in layers.zones)
    assert all(isinstance(obstacle, Obstacle) for obstacle in layers.obstacles)
    assert all(obstacle.height_m > 0 for obstacle in layers.obstacles)
