"""Выбор формата выгрузки и запись файлов."""

from __future__ import annotations

from pathlib import Path

from core.export.geojson import write_geojson
from core.export.kml import write_kml
from core.export.schedule import write_schedule
from core.geo.project import Projector
from core.schemas import ExportFormat, PipelineResult, Scenario

#: Расширение файла → формат выгрузки.
SUFFIX_TO_FORMAT: dict[str, ExportFormat] = {
    ".geojson": ExportFormat.GEOJSON,
    ".json": ExportFormat.GEOJSON,
    ".kml": ExportFormat.KML,
    ".csv": ExportFormat.SCHEDULE,
}

#: Формат выгрузки → окончание имени файла.
FORMAT_TO_SUFFIX: dict[ExportFormat, str] = {
    ExportFormat.GEOJSON: ".geojson",
    ExportFormat.KML: ".kml",
    ExportFormat.SCHEDULE: ".schedule.csv",
}


def format_for(path: Path) -> ExportFormat:
    """Формат по расширению файла; неизвестное расширение — GeoJSON."""
    return SUFFIX_TO_FORMAT.get(path.suffix.lower(), ExportFormat.GEOJSON)


def output_path_for(directory: Path, scenario_id: str, export_format: ExportFormat) -> Path:
    """Путь файла выгрузки внутри каталога: `<сценарий><окончание формата>`."""
    return directory / f"{scenario_id}{FORMAT_TO_SUFFIX[export_format]}"


def export_mission(
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    path: Path,
    export_format: ExportFormat | None = None,
) -> Path:
    """Выгрузить итог конвейера в файл.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь выходного файла.
        export_format: формат. None — определить по расширению пути.

    Returns:
        Путь записанного файла.
    """
    chosen = export_format or format_for(path)
    if chosen is ExportFormat.KML:
        return write_kml(result, scenario, projector, path)
    if chosen is ExportFormat.SCHEDULE:
        return write_schedule(result.mission, scenario, path)
    return write_geojson(result, scenario, projector, path)


__all__ = [
    "FORMAT_TO_SUFFIX",
    "SUFFIX_TO_FORMAT",
    "export_mission",
    "format_for",
    "output_path_for",
]
