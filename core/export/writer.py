"""Выбор формата выгрузки и запись файлов."""

from __future__ import annotations

from pathlib import Path

from core.export.geojson import write_geojson
from core.export.kml import write_kml
from core.geo.project import Projector
from core.schemas import ExportFormat, Mission, Scenario

#: Расширение файла → формат выгрузки.
SUFFIX_TO_FORMAT: dict[str, ExportFormat] = {
    ".geojson": ExportFormat.GEOJSON,
    ".json": ExportFormat.GEOJSON,
    ".kml": ExportFormat.KML,
}

#: Формат выгрузки → расширение файла.
FORMAT_TO_SUFFIX: dict[ExportFormat, str] = {
    ExportFormat.GEOJSON: ".geojson",
    ExportFormat.KML: ".kml",
}


def format_for(path: Path) -> ExportFormat:
    """Определить формат по расширению файла.

    Args:
        path: путь выходного файла.

    Returns:
        Формат выгрузки. Неизвестное расширение трактуется как GeoJSON.
    """
    return SUFFIX_TO_FORMAT.get(path.suffix.lower(), ExportFormat.GEOJSON)


def output_path_for(directory: Path, scenario_id: str, export_format: ExportFormat) -> Path:
    """Путь файла выгрузки внутри каталога.

    Args:
        directory: каталог назначения.
        scenario_id: идентификатор сценария — он же имя файла.
        export_format: формат выгрузки.

    Returns:
        Путь файла.
    """
    return directory / f"{scenario_id}{FORMAT_TO_SUFFIX[export_format]}"


def export_mission(
    mission: Mission,
    scenario: Scenario,
    projector: Projector,
    path: Path,
    export_format: ExportFormat | None = None,
) -> Path:
    """Выгрузить полётное задание в файл.

    Args:
        mission: готовый план.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        path: путь выходного файла.
        export_format: формат. None — определить по расширению пути.

    Returns:
        Путь записанного файла.
    """
    chosen = export_format or format_for(path)
    if chosen is ExportFormat.KML:
        return write_kml(mission, scenario, projector, path)
    return write_geojson(mission, scenario, projector, path)


__all__ = [
    "FORMAT_TO_SUFFIX",
    "SUFFIX_TO_FORMAT",
    "export_mission",
    "format_for",
    "output_path_for",
]
