"""Исходные файлы сценариев: из чего собран каждый сценарий (E1, E10).

Сценарий приходит так, как его отдаёт заказчик: область съёмки, зоны
ограничений и высотные препятствия — слоями KML, параметры задания — JSON
(docs/input_formats.md). Собранный JSON сценария лежит рядом: его читают CLI,
тесты и API. Какой сценарий из каких файлов собран — манифест
`scenarios/sources/manifest.json`; совпадение JSON с тем, что собирается из
файлов, держит тест (`tests/test_sources.py`).

Пути в манифесте — от каталога самого манифеста: исходники тестовых
сценариев лежат рядом (`scenarios/sources/<id>/`), файлы заказчика — в
`data/samples/` под своими именами и не копируются.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry

from core.errors import ScenarioError
from core.geo.kml_import import import_layers
from core.schemas import GeoPolygon, ImportReport, Obstacle, Scenario

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Манифест по умолчанию. Не в корне `scenarios/`: там каждый JSON — сценарий.
DEFAULT_MANIFEST = REPO_ROOT / "scenarios" / "sources" / "manifest.json"

#: Виды исходных файлов: три слоя и параметры задания.
SOURCE_KINDS = ("area", "zones", "obstacles", "params")

#: Допуск сравнения координат, градусы (~1 мм): импорт переводит область в
#: метры и обратно, и последние знаки координат от этого шумят.
_COORD_TOLERANCE_DEG = 1.0e-8

#: Допуск сравнения высот, метры.
_HEIGHT_TOLERANCE_M = 1.0e-6


@dataclass(frozen=True)
class ScenarioSources:
    """Исходные файлы одного сценария."""

    scenario_id: str
    scenario: Path
    """Собранный JSON сценария."""
    files: dict[str, Path] = field(default_factory=dict)
    """Исходники по виду: `area`, `zones`, `obstacles`, `params`."""
    report: Path | None = None
    """Сохранённый отчёт импорта; None — считается по файлам."""
    planning_available: bool = True
    """Можно ли запускать расчёт из интерфейса."""
    planning_note: str | None = None
    """Почему расчёт недоступен или чем он особенный — для пользователя."""


def load_sources(path: Path | None = None) -> dict[str, ScenarioSources]:
    """Прочитать манифест исходников сценариев.

    Args:
        path: файл манифеста; None — `scenarios/sources/manifest.json`.

    Returns:
        Исходники по идентификатору сценария. Манифеста нет — пусто.

    Raises:
        ScenarioError: манифест не разбирается или ссылается на несуществующий
            файл — это ошибка репозитория, её надо видеть сразу.
    """
    manifest = path or DEFAULT_MANIFEST
    if not manifest.is_file():
        return {}
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ScenarioError(f"Манифест {manifest.name} не разбирается: {error}") from error

    root = manifest.resolve().parent
    result: dict[str, ScenarioSources] = {}
    for scenario_id, entry in raw.get("scenarios", {}).items():

        def resolve(relative: str, scenario_id: str = scenario_id) -> Path:
            resolved = (root / relative).resolve()
            if not resolved.is_file():
                raise ScenarioError(f"Манифест: у сценария {scenario_id!r} нет файла {relative}")
            return resolved

        planning = entry.get("planning", {})
        result[scenario_id] = ScenarioSources(
            scenario_id=scenario_id,
            scenario=resolve(entry["scenario"]),
            files={kind: resolve(entry[kind]) for kind in SOURCE_KINDS if entry.get(kind)},
            report=resolve(entry["report"]) if entry.get("report") else None,
            planning_available=bool(planning.get("available", True)),
            planning_note=planning.get("note"),
        )
    return result


def build_from_sources(sources: ScenarioSources) -> tuple[Scenario, ImportReport]:
    """Собрать сценарий из исходных файлов — так же, как `import-layers`.

    Returns:
        Сценарий и отчёт импорта.

    Raises:
        ScenarioError: нет области или файла параметров.
        GeometryError: слой не читается.
    """
    if "area" not in sources.files or "params" not in sources.files:
        raise ScenarioError(
            f"Сценарий {sources.scenario_id!r}: для сборки нужны область и параметры задания"
        )
    payload = json.loads(sources.files["params"].read_text(encoding="utf-8"))
    layers = import_layers(
        area_source=sources.files["area"],
        zones_source=sources.files.get("zones"),
        obstacles_source=sources.files.get("obstacles"),
    )
    payload["area"] = layers.area.model_dump(mode="json")
    payload["no_fly_zones"] = [zone.model_dump(mode="json") for zone in layers.zones]
    payload["obstacles"] = [obstacle.model_dump(mode="json") for obstacle in layers.obstacles]
    return Scenario.model_validate(payload), layers.report


def import_report(sources: ScenarioSources) -> ImportReport:
    """Отчёт импорта: сохранённый рядом со сценарием или посчитанный по файлам."""
    if sources.report is not None:
        return ImportReport.model_validate_json(sources.report.read_text(encoding="utf-8"))
    return build_from_sources(sources)[1]


# --------------------------------------------------------------------------
# Сравнение: тот ли это сценарий
# --------------------------------------------------------------------------


def _shape(polygon: GeoPolygon) -> Polygon:
    return Polygon(
        [(point.lon, point.lat) for point in polygon.outer],
        [[(point.lon, point.lat) for point in hole] for hole in polygon.holes],
    )


def _same_shape(left: BaseGeometry, right: BaseGeometry) -> bool:
    """Фигуры совпадают с точностью до шума координат, градусы WGS84."""
    if left.is_empty or right.is_empty:
        return left.is_empty and right.is_empty
    return left.hausdorff_distance(right) <= _COORD_TOLERANCE_DEG


def _obstacle_shape(obstacle: Obstacle) -> BaseGeometry:
    if obstacle.position is not None:
        return ShapelyPoint(obstacle.position.lon, obstacle.position.lat)
    return _shape(obstacle.polygon)  # type: ignore[arg-type]


def scenario_differences(built: Scenario, stored: Scenario) -> list[str]:
    """Чем собранный из файлов сценарий отличается от сохранённого JSON.

    Координаты сравниваются с допуском: импорт переводит область в метры и
    обратно. Нижняя граница зоны «от земли» — 0 или отсутствует — одно и то же.

    Returns:
        Список расхождений словами; пусто — сценарии совпадают.
    """
    differences: list[str] = []
    params_built = built.model_dump(mode="json", exclude={"area", "no_fly_zones", "obstacles"})
    params_stored = stored.model_dump(mode="json", exclude={"area", "no_fly_zones", "obstacles"})
    for key in sorted(set(params_built) | set(params_stored)):
        if params_built.get(key) != params_stored.get(key):
            differences.append(
                f"параметр {key}: {params_built.get(key)!r} ≠ {params_stored.get(key)!r}"
            )

    area_built = MultiPolygon([_shape(polygon) for polygon in built.area.polygons])
    area_stored = MultiPolygon([_shape(polygon) for polygon in stored.area.polygons])
    if not _same_shape(area_built, area_stored):
        differences.append("область съёмки: контуры различаются")

    zones_built = {zone.id: zone for zone in built.no_fly_zones}
    zones_stored = {zone.id: zone for zone in stored.no_fly_zones}
    if set(zones_built) != set(zones_stored):
        differences.append(f"зоны: {sorted(zones_built)} ≠ {sorted(zones_stored)}")
    for zone_id in sorted(set(zones_built) & set(zones_stored)):
        left, right = zones_built[zone_id], zones_stored[zone_id]
        if (left.name, left.max_alt_m, left.active_from, left.active_to) != (
            right.name,
            right.max_alt_m,
            right.active_from,
            right.active_to,
        ) or abs((left.min_alt_m or 0.0) - (right.min_alt_m or 0.0)) > _HEIGHT_TOLERANCE_M:
            differences.append(f"зона {zone_id}: имя, высоты или окно различаются")
        if not _same_shape(_shape(left.polygon), _shape(right.polygon)):
            differences.append(f"зона {zone_id}: контуры различаются")

    obstacles_built = {item.id: item for item in built.obstacles}
    obstacles_stored = {item.id: item for item in stored.obstacles}
    if set(obstacles_built) != set(obstacles_stored):
        differences.append(
            f"препятствия: {len(obstacles_built)} собрано, {len(obstacles_stored)} в JSON"
        )
    for obstacle_id in sorted(set(obstacles_built) & set(obstacles_stored)):
        left, right = obstacles_built[obstacle_id], obstacles_stored[obstacle_id]
        if left.name != right.name or abs(left.height_m - right.height_m) > _HEIGHT_TOLERANCE_M:
            differences.append(f"препятствие {obstacle_id}: имя или высота различаются")
        elif not _same_shape(_obstacle_shape(left), _obstacle_shape(right)):
            differences.append(f"препятствие {obstacle_id}: геометрия различается")
    return differences


__all__ = [
    "DEFAULT_MANIFEST",
    "SOURCE_KINDS",
    "ScenarioSources",
    "build_from_sources",
    "import_report",
    "load_sources",
    "scenario_differences",
]
