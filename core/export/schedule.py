"""Расписание вылетов: строки для всех форматов и CSV (заказчик, №31).

Строки расписания собираются здесь один раз и уходят в три места: CSV, папку
«Расписание» в KML и свойство `schedule` коллекции GeoJSON. Так файлы одного
расчёта не расходятся между собой.

Отдельный CSV остаётся, хотя расписание есть и в KML: расписание читают люди на
площадке и загружают в таблицы, а CSV открывается чем угодно (заказчик, №21:
«можете предложить доп. файлы с обоснованием»). Состав колонок —
docs/export_format.md §5.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from core.export.boards import board_id, select_boards
from core.export.common import utc_time
from core.schemas import Mission, Scenario


@dataclass(frozen=True)
class ScheduleRow:
    """Строка расписания: один вылет. Поля — колонки CSV в порядке вывода."""

    sortie_id: str
    board_id: str
    vehicle_id: str
    vehicle_instance: int
    """Номер экземпляра модели, с нуля (в `board_id` — с единицы)."""
    trip_index: int
    """Номер вылета борта, с нуля."""
    takeoff_site_id: str
    landing_site_id: str
    start_s: int
    """Взлёт от начала работ, секунды."""
    end_s: int
    """Посадка от начала работ, секунды."""
    duration_min: float
    start_time_utc: str | None
    """Взлёт, UTC; None — начало работ не задано."""
    end_time_utc: str | None
    delay_s: int
    """Сдвиг старта ради разведения бортов и окон временных зон, секунды."""
    transit_altitude_m: int
    """Эшелон перелёта, метры AGL."""

    def as_dict(self) -> dict[str, object]:
        """Строка как словарь колонок — для GeoJSON и ExtendedData KML."""
        return asdict(self)


#: Колонки расписания в порядке вывода.
COLUMNS: tuple[str, ...] = tuple(item.name for item in fields(ScheduleRow))


def schedule_rows(
    mission: Mission, scenario: Scenario, board: str | None = None
) -> list[ScheduleRow]:
    """Строки расписания по времени взлёта. У невыполнимого плана — пусто.

    Args:
        mission: итог конвейера.
        scenario: сценарий (время начала работ).
        board: идентификатор борта (`board_id`); None — все борта.

    Returns:
        Строки: время — целые секунды от начала работ и, если начало задано,
        UTC; длительность — минуты; высота — метры AGL.

    Raises:
        ExportError: борта с таким идентификатором в плане нет.
    """
    wanted = {item.id for item in select_boards(mission, board)}
    entries = mission.schedule.entries if mission.schedule else []
    rows: list[ScheduleRow] = []
    for entry in sorted(entries, key=lambda item: (item.start_s, item.sortie_id)):
        entry_board = board_id(entry.vehicle_id, entry.vehicle_instance)
        if entry_board not in wanted:
            continue
        rows.append(
            ScheduleRow(
                sortie_id=entry.sortie_id,
                board_id=entry_board,
                vehicle_id=entry.vehicle_id,
                vehicle_instance=entry.vehicle_instance,
                trip_index=entry.trip_index,
                takeoff_site_id=entry.takeoff_site_id,
                landing_site_id=entry.landing_site_id,
                start_s=round(entry.start_s),
                end_s=round(entry.end_s),
                duration_min=round((entry.end_s - entry.start_s) / 60, 1),
                start_time_utc=utc_time(scenario.start_time, entry.start_s),
                end_time_utc=utc_time(scenario.start_time, entry.end_s),
                delay_s=round(entry.delay_s),
                transit_altitude_m=round(entry.transit_altitude_m),
            )
        )
    return rows


def schedule_to_csv(mission: Mission, scenario: Scenario, board: str | None = None) -> str:
    """Расписание в CSV. У невыполнимого плана — только строка заголовка.

    Args:
        mission: итог конвейера.
        scenario: сценарий (время начала работ).
        board: идентификатор борта (`board_id`); None — все борта.

    Returns:
        Текст CSV, разделитель — запятая, кодировка при записи — UTF-8. Пустое
        время UTC — начало работ не задано.

    Raises:
        ExportError: борта с таким идентификатором в плане нет.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in schedule_rows(mission, scenario, board):
        writer.writerow(
            {key: "" if value is None else value for key, value in row.as_dict().items()}
        )
    return buffer.getvalue()


def write_schedule(
    mission: Mission, scenario: Scenario, path: Path, board: str | None = None
) -> Path:
    """Записать расписание в CSV.

    Args:
        mission: итог конвейера.
        scenario: сценарий.
        path: путь файла.
        board: идентификатор борта; None — все борта.

    Returns:
        Путь записанного файла.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schedule_to_csv(mission, scenario, board), encoding="utf-8")
    return path


__all__ = ["COLUMNS", "ScheduleRow", "schedule_rows", "schedule_to_csv", "write_schedule"]
