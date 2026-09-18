"""Расписание вылетов в CSV (заказчик, №31).

Отдельный файл, а не только метки времени в KML: расписание читают люди на
площадке и загружают в таблицы — CSV открывается чем угодно. Обоснование и
состав колонок — docs/export_format.md (заказчик, №21: «можете предложить
доп. файлы с обоснованием»).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from core.export.geojson import iso_time
from core.schemas import Mission, Scenario

#: Колонки CSV в порядке вывода.
COLUMNS = (
    "sortie_id",
    "vehicle_id",
    "vehicle_instance",
    "trip_index",
    "takeoff_site_id",
    "landing_site_id",
    "start_s",
    "end_s",
    "duration_min",
    "start_time_utc",
    "end_time_utc",
    "delay_s",
    "transit_altitude_m",
)


def schedule_to_csv(mission: Mission, scenario: Scenario) -> str:
    """Расписание в CSV. У невыполнимого плана — только строка заголовка.

    Args:
        mission: итог конвейера.
        scenario: сценарий (время начала работ).

    Returns:
        Текст CSV, разделитель — запятая, кодировка при записи — UTF-8.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    entries = mission.schedule.entries if mission.schedule else []
    for entry in entries:
        writer.writerow(
            [
                entry.sortie_id,
                entry.vehicle_id,
                entry.vehicle_instance,
                entry.trip_index,
                entry.takeoff_site_id,
                entry.landing_site_id,
                f"{entry.start_s:.0f}",
                f"{entry.end_s:.0f}",
                f"{(entry.end_s - entry.start_s) / 60:.1f}",
                iso_time(scenario.start_time, entry.start_s) or "",
                iso_time(scenario.start_time, entry.end_s) or "",
                f"{entry.delay_s:.0f}",
                f"{entry.transit_altitude_m:.0f}",
            ]
        )
    return buffer.getvalue()


def write_schedule(mission: Mission, scenario: Scenario, path: Path) -> Path:
    """Записать расписание в CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schedule_to_csv(mission, scenario), encoding="utf-8")
    return path


__all__ = ["COLUMNS", "schedule_to_csv", "write_schedule"]
