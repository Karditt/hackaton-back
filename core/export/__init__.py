"""Экспорт (E8): GeoJSON, KML, расписание в CSV, задания бортов.

WGS84 (заказчик, №10, №21). Высоты — над поверхностью, KML — `relativeToGround`.
Выполнимый план: маршруты по фазам, ключевые точки, трек `gx:Track` и
расписание с метками времени, подобласти, зоны манёвра, площадки; на весь план
и отдельно на каждый борт (ТЗ: «индивидуальные полётные задания»). Расписание —
ещё и отдельным CSV (№31). Невыполнимый план — отчёт: причины с местами
нарушений и рекомендации, маршрутов нет (инвариант 10). Структура файлов
описана в docs/export_format.md — это требование заказчика (№21:
«задокументируйте, как всё будет храниться»).

Не реализовано: форматы наземных станций (`.plan`, `.waypoints`) — P2.

ЗДЕСЬ ВТОРАЯ ГРАНИЦА ИНВАРИАНТА 1: метры переводятся обратно в WGS84 при
выгрузке.
"""

from __future__ import annotations

from core.export.boards import Board, KeyPoint, KeyPointKind, boards_of, sortie_keypoints
from core.export.geojson import (
    FORMAT_VERSION,
    geojson_to_text,
    mission_to_geojson,
    scenario_to_geojson,
    write_geojson,
)
from core.export.kml import kml_to_bytes, mission_to_kml, write_kml
from core.export.package import board_file_name, mission_files, mission_zip, write_mission_files
from core.export.schedule import ScheduleRow, schedule_rows, schedule_to_csv, write_schedule
from core.export.writer import export_mission, output_path_for

__all__ = [
    "FORMAT_VERSION",
    "Board",
    "KeyPoint",
    "KeyPointKind",
    "ScheduleRow",
    "board_file_name",
    "boards_of",
    "export_mission",
    "geojson_to_text",
    "kml_to_bytes",
    "mission_files",
    "mission_to_geojson",
    "mission_to_kml",
    "mission_zip",
    "output_path_for",
    "scenario_to_geojson",
    "schedule_rows",
    "schedule_to_csv",
    "sortie_keypoints",
    "write_geojson",
    "write_kml",
    "write_mission_files",
    "write_schedule",
]
