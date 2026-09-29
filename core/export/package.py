"""Комплект файлов расчёта: обзор плана и индивидуальные задания бортов.

ТЗ требует «индивидуальные полётные задания… для каждого БВС». Комплект
одного расчёта (docs/export_format.md §1):

    <сценарий>.geojson             весь план — программам и карте веба
    <сценарий>.kml                 весь план — обзор для ГИС и Google Earth
    <сценарий>.schedule.csv        расписание всех вылетов — таблица
    <сценарий>.<board_id>.kml      задание одного борта — его вылеты, ключевые
                                   точки, трек, расписание и слои сценария

Один и тот же состав пишет CLI в каталог и отдаёт API архивом ZIP. У
невыполнимого плана бортовых заданий нет: только отчёт (инвариант 10).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping
from pathlib import Path

from core.export.boards import boards_of
from core.export.geojson import geojson_to_text, mission_to_geojson
from core.export.kml import kml_to_bytes, mission_to_kml
from core.export.schedule import schedule_to_csv
from core.geo.project import Projector
from core.schemas import PipelineResult, Scenario

#: Метка времени записей архива. Постоянная, чтобы один расчёт давал
#: побитово один и тот же архив (docs/dev/AGENT.md §8); 1980-01-01 — начало эпохи ZIP.
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(name: str) -> str:
    """Имя без символов, недопустимых в именах файлов и в URL."""
    return _UNSAFE.sub("_", name) or "_"


def file_stem(scenario_id: str, board: str | None = None) -> str:
    """Основа имени файла: `<сценарий>` или `<сценарий>.<board_id>` для борта."""
    stem = _safe(scenario_id)
    return stem if board is None else f"{stem}.{_safe(board)}"


def board_file_name(scenario_id: str, board: str) -> str:
    """Имя файла задания борта: `<сценарий>.<board_id>.kml`."""
    return f"{file_stem(scenario_id, board)}.kml"


def mission_files(
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    names: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    """Все файлы расчёта в памяти: имя → содержимое.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        names: название модели по идентификатору — подписи бортов в файлах.

    Returns:
        Обзорные GeoJSON, KML, расписание CSV и KML на каждый борт плана.
    """
    stem = file_stem(scenario.id)
    files = {
        f"{stem}.geojson": geojson_to_text(
            mission_to_geojson(result, scenario, projector, names=names)
        ).encode("utf-8"),
        f"{stem}.kml": kml_to_bytes(mission_to_kml(result, scenario, projector, names=names)),
        f"{stem}.schedule.csv": schedule_to_csv(result.mission, scenario).encode("utf-8"),
    }
    for board in boards_of(result.mission):
        files[board_file_name(scenario.id, board.id)] = kml_to_bytes(
            mission_to_kml(result, scenario, projector, board.id, names)
        )
    return files


def write_mission_files(
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    directory: Path,
    names: Mapping[str, str] | None = None,
) -> list[Path]:
    """Записать комплект файлов расчёта в каталог.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        directory: каталог выгрузки.
        names: название модели по идентификатору — подписи бортов в файлах.

    Returns:
        Пути записанных файлов.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in mission_files(result, scenario, projector, names).items():
        path = directory / name
        path.write_bytes(content)
        written.append(path)
    return written


def mission_zip(
    result: PipelineResult,
    scenario: Scenario,
    projector: Projector,
    names: Mapping[str, str] | None = None,
) -> bytes:
    """Комплект файлов расчёта одним архивом ZIP.

    Args:
        result: все стадии прогона.
        scenario: исходный сценарий.
        projector: преобразователь рабочей проекции в WGS84.
        names: название модели по идентификатору — подписи бортов в файлах.

    Returns:
        Содержимое архива: те же файлы, что пишет CLI в каталог.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in mission_files(result, scenario, projector, names).items():
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()


__all__ = ["board_file_name", "file_stem", "mission_files", "mission_zip", "write_mission_files"]
