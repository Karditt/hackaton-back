"""Новый сценарий из своих файлов: слой площадок и подбор мест площадок (E1, E15, №28).

Пользователь приносит область, зоны и препятствия слоями KML; площадки —
точками KML, в параметрах задания или никак — тогда места подбирает сервис.
"""

from __future__ import annotations

import json
import math

import pytest
from shapely.geometry import Point

from core import assumptions
from core.errors import GeometryError
from core.geo.kml_import import import_layers, read_sites
from core.geo.mask import build_work_area
from core.schemas import SiteKind
from core.sites import place_sites
from core.sources import assemble_scenario, load_sources

SOURCES = load_sources()

_SITES_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>Юг</name><Point><coordinates>30.30,59.90,0</coordinates></Point></Placemark>
  <Placemark><name>Резерв у дороги</name>
    <Point><coordinates>30.31,59.91</coordinates></Point></Placemark>
  <Placemark><name>Склад</name>
    <ExtendedData><Data name="site_id"><value>depot</value></Data>
    <Data name="site_kind"><value>primary</value></Data></ExtendedData>
    <Point><coordinates>30.32,59.92</coordinates></Point></Placemark>
  <Placemark><name>Контур</name><LineString><coordinates>30,59 31,60</coordinates></LineString>
  </Placemark>
</Document></kml>"""


def _layers_and_params(scenario_id: str) -> tuple:
    item = SOURCES[scenario_id]
    params = json.loads(item.files["params"].read_text(encoding="utf-8"))
    layers = import_layers(item.files["area"], item.files.get("zones"), item.files.get("obstacles"))
    return layers, params


def test_sites_are_read_from_points() -> None:
    """Точка — площадка; «резерв» в имени — резервная; метка без точки — в отчёт."""
    sites, report = read_sites(_SITES_KML)
    assert [site.id for site in sites] == ["site-1", "site-2", "depot"]
    assert [site.name for site in sites] == ["Юг", "Резерв у дороги", "Склад"]
    assert [site.kind for site in sites] == [SiteKind.PRIMARY, SiteKind.RESERVE, SiteKind.PRIMARY]
    assert sites[0].position.lon == pytest.approx(30.30)
    assert (report.sites.read, report.sites.kept, report.sites.dropped) == (4, 3, 1)
    assert [warning.code for warning in report.warnings] == ["import.site_dropped"]


def test_sites_file_without_points_is_rejected() -> None:
    """Файл площадок без единой точки — ошибка входа, а не сценарий без площадок."""
    with pytest.raises(GeometryError, match="площадок"):
        read_sites("<kml><Document><Placemark><name>x</name></Placemark></Document></kml>")


def test_sites_are_placed_when_none_given() -> None:
    """Без площадок сервис ставит их сам: не в зонах, у каждой точки области — ближе радиуса.

    Демо — 95 км² с зонами и препятствиями: площадок больше одной, но не больше предела.
    """
    layers, params = _layers_and_params("demo_100km2")
    params.pop("sites")
    scenario, report = assemble_scenario(params, layers)
    sites = scenario.sites
    assert 1 < len(sites) <= assumptions.AUTO_SITES_MAX
    assert all(site.kind is SiteKind.PRIMARY for site in sites)
    assert len({site.name for site in sites}) == len(sites)
    codes = {warning.code for warning in report.warnings}
    assert "import.sites_auto" in codes
    # Приписка Gemini к площадке демо снята: такой площадки больше нет.
    assert "import.site_unpinned" in codes
    assert all(unit.site_id is None for unit in scenario.fleet)

    work_area = build_work_area(scenario)
    for site in sites:
        point = work_area.site(site.id)
        spot = Point(point.x_m, point.y_m)
        assert not any(zone.buffered.contains(spot) for zone in work_area.zones)
        assert not any(item.buffered.contains(spot) for item in work_area.obstacles)
    # Любая вершина области — не дальше радиуса от ближайшей площадки (с запасом на
    # уточнение мест: площадка в центре круга своих точек, а не ровно в радиусе).
    points = [work_area.site(site.id) for site in sites]
    corners = [
        c
        for part in getattr(work_area.area, "geoms", [work_area.area])
        for c in part.exterior.coords
    ]
    farthest = max(min(math.hypot(x - p.x_m, y - p.y_m) for p in points) for x, y in corners)
    assert farthest <= assumptions.AUTO_SITE_RADIUS_M * 1.5

    again, _ = assemble_scenario(params, layers)
    assert again.sites == sites, "подбор детерминирован"


def test_small_area_gets_one_central_site() -> None:
    """Маленькая область — одна площадка, «Центральная», внутри описанного круга области."""
    layers, params = _layers_and_params("simple")
    params.pop("sites")
    scenario, _ = assemble_scenario(params, layers)
    assert [site.name for site in scenario.sites] == ["Центральная"]
    work_area = build_work_area(scenario)
    site = work_area.site(scenario.sites[0].id)
    assert work_area.area.buffer(assumptions.AUTO_SITE_RADIUS_M).contains(Point(site.x_m, site.y_m))


def test_sites_layer_replaces_params_sites() -> None:
    """Слой площадок сильнее площадок параметров; приписка к исчезнувшей — снимается."""
    layers, params = _layers_and_params("simple")
    params["fleet"][0]["site_id"] = params["sites"][0]["id"]
    scenario, report = assemble_scenario(params, layers, _SITES_KML)
    assert [site.id for site in scenario.sites] == ["site-1", "site-2", "depot"]
    assert report.sites.kept == 3
    assert scenario.fleet[0].site_id is None
    assert "import.site_unpinned" in {warning.code for warning in report.warnings}


def test_place_sites_respects_the_limit() -> None:
    """Площадок не больше заданного предела, даже если до края дальше радиуса."""
    layers, params = _layers_and_params("real_msk")
    scenario, _ = assemble_scenario(params, layers)
    sites = place_sites(build_work_area(scenario), radius_m=1_000.0, max_sites=2)
    assert len(sites) == 2
