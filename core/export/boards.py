"""Борта и ключевые точки — то, из чего собирается индивидуальное задание.

ТЗ требует «индивидуальные полётные задания… для каждого БВС» и «маршрут и
ключевые точки». Здесь — оба понятия, общие для KML, GeoJSON и расписания:

* борт — физический экземпляр модели из флота: пара (модель, номер экземпляра).
  Его идентификатор `board_id` — `<модель>_<номер с единицы>`, например
  `geoscan-gemini_3`: так же борт подписан в интерфейсе («geoscan-gemini #3»),
  и так же называется его файл задания. Определение одно на весь проект —
  `core.allocate.fleet.board_id`: из него же строится `sortie_id` вылета;
* ключевые точки вылета — границы этапов полёта, которые называл заказчик
  (№30: взлёт, перелёт, площадная съёмка, посадка): взлёт, начало и конец
  съёмки каждой подобласти, посадка. Время точек взлёта и посадки совпадает с
  расписанием (`start_s`, `end_s`), чтобы файлы не расходились между собой.

Модуль ничего не считает заново: всё берётся из точек маршрута вылета.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.allocate.fleet import board_id
from core.errors import ExportError
from core.schemas import FlightPhase, Mission, Sortie


def board_label(vehicle_id: str, vehicle_instance: int) -> str:
    """Подпись борта для человека — как в интерфейсе: `geoscan-gemini #3`."""
    return f"{vehicle_id} #{vehicle_instance + 1}"


def sortie_board_id(sortie: Sortie) -> str:
    """Идентификатор борта, выполняющего вылет."""
    return board_id(sortie.vehicle_id, sortie.vehicle_instance)


@dataclass(frozen=True)
class Board:
    """Борт с его вылетами в порядке взлёта."""

    id: str
    label: str
    vehicle_id: str
    vehicle_instance: int
    sorties: tuple[Sortie, ...]

    @property
    def flight_time_s(self) -> float:
        """Суммарное полётное время борта, секунды."""
        return sum(sortie.duration_s for sortie in self.sorties)

    @property
    def first_takeoff_s(self) -> float:
        """Первый взлёт от начала работ, секунды."""
        return self.sorties[0].start_time_s

    @property
    def last_landing_s(self) -> float:
        """Последняя посадка от начала работ, секунды."""
        return max(sortie.end_time_s() for sortie in self.sorties)

    def summary(self) -> dict[str, object]:
        """Сводка борта для выгрузки и API: идентификатор, подпись, объём работы."""
        return {
            "board_id": self.id,
            "label": self.label,
            "vehicle_id": self.vehicle_id,
            "vehicle_instance": self.vehicle_instance,
            "sortie_count": len(self.sorties),
            "flight_time_s": round(self.flight_time_s, 1),
            "first_takeoff_s": round(self.first_takeoff_s, 1),
            "last_landing_s": round(self.last_landing_s, 1),
        }


def boards_of(mission: Mission) -> list[Board]:
    """Борта, получившие вылеты, по модели и номеру экземпляра.

    У невыполнимого плана вылетов нет, и список пуст (инвариант 10).

    Args:
        mission: итог конвейера.

    Returns:
        Борта; вылеты каждого — в порядке взлёта.
    """
    grouped: dict[tuple[str, int], list[Sortie]] = {}
    for sortie in mission.sorties:
        grouped.setdefault((sortie.vehicle_id, sortie.vehicle_instance), []).append(sortie)
    return [
        Board(
            id=board_id(vehicle_id, instance),
            label=board_label(vehicle_id, instance),
            vehicle_id=vehicle_id,
            vehicle_instance=instance,
            sorties=tuple(sorted(sorties, key=lambda item: (item.start_time_s, item.trip_index))),
        )
        for (vehicle_id, instance), sorties in sorted(grouped.items())
    ]


def select_boards(mission: Mission, board: str | None) -> list[Board]:
    """Борта для выгрузки: все либо один.

    Args:
        mission: итог конвейера.
        board: идентификатор борта (`board_id`); None — все борта.

    Returns:
        Выбранные борта.

    Raises:
        ExportError: борта с таким идентификатором в плане нет.
    """
    boards = boards_of(mission)
    if board is None:
        return boards
    for item in boards:
        if item.id == board:
            return [item]
    known = ", ".join(item.id for item in boards) or "нет — план невыполним"
    raise ExportError(f"Борта {board!r} в плане нет. Борта плана: {known}")


class KeyPointKind(str, Enum):
    """Ключевая точка вылета — граница этапов полёта (заказчик, №30)."""

    TAKEOFF = "takeoff"
    SURVEY_START = "survey_start"
    SURVEY_END = "survey_end"
    LANDING = "landing"


#: Подписи ключевых точек для человека.
KEYPOINT_TITLES: dict[KeyPointKind, str] = {
    KeyPointKind.TAKEOFF: "Взлёт",
    KeyPointKind.SURVEY_START: "Начало съёмки",
    KeyPointKind.SURVEY_END: "Конец съёмки",
    KeyPointKind.LANDING: "Посадка",
}


@dataclass(frozen=True)
class KeyPoint:
    """Ключевая точка вылета в рабочей проекции, метры и секунды."""

    kind: KeyPointKind
    sortie_id: str
    x_m: float
    y_m: float
    alt_m: float
    """Высота над поверхностью (AGL), метры."""
    time_s: float
    """Момент от начала работ, секунды."""
    cell_id: str | None = None
    """Подобласть — у точек начала и конца съёмки."""
    site_id: str | None = None
    """Площадка — у точек взлёта и посадки."""

    @property
    def title(self) -> str:
        """Подпись точки: «Взлёт», «Начало съёмки c-3» и т. п."""
        base = KEYPOINT_TITLES[self.kind]
        return f"{base} {self.cell_id}" if self.cell_id else base


def sortie_keypoints(sortie: Sortie) -> list[KeyPoint]:
    """Ключевые точки вылета в порядке времени.

    Взлёт — начало фазы взлёта на площадке, посадка — конец фазы посадки: это
    те же моменты, что `start_s` и `end_s` в расписании. Начало и конец съёмки —
    первая точка первого галса и последняя точка последнего галса подобласти.

    Args:
        sortie: вылет на общей оси времени.

    Returns:
        Взлёт, пары «начало — конец съёмки» по подобластям, посадка.
    """
    if not sortie.legs:
        return []
    first = sortie.legs[0].waypoints[0]
    last = sortie.legs[-1].waypoints[-1]
    points = [
        KeyPoint(
            kind=KeyPointKind.TAKEOFF,
            sortie_id=sortie.id,
            x_m=first.x_m,
            y_m=first.y_m,
            alt_m=first.alt_m,
            time_s=first.time_s,
            site_id=sortie.takeoff_site_id,
        )
    ]
    for cell_id in sortie.cell_ids:
        strips = [
            leg for leg in sortie.legs if leg.phase is FlightPhase.STRIP and leg.cell_id == cell_id
        ]
        if not strips:
            continue
        start = strips[0].waypoints[0]
        end = strips[-1].waypoints[-1]
        points.append(
            KeyPoint(
                kind=KeyPointKind.SURVEY_START,
                sortie_id=sortie.id,
                x_m=start.x_m,
                y_m=start.y_m,
                alt_m=start.alt_m,
                time_s=start.time_s,
                cell_id=cell_id,
            )
        )
        points.append(
            KeyPoint(
                kind=KeyPointKind.SURVEY_END,
                sortie_id=sortie.id,
                x_m=end.x_m,
                y_m=end.y_m,
                alt_m=end.alt_m,
                time_s=end.time_s,
                cell_id=cell_id,
            )
        )
    points.append(
        KeyPoint(
            kind=KeyPointKind.LANDING,
            sortie_id=sortie.id,
            x_m=last.x_m,
            y_m=last.y_m,
            alt_m=last.alt_m,
            time_s=last.time_s,
            site_id=sortie.landing_site_id,
        )
    )
    return sorted(points, key=lambda point: point.time_s)


__all__ = [
    "KEYPOINT_TITLES",
    "Board",
    "KeyPoint",
    "KeyPointKind",
    "board_id",
    "board_label",
    "boards_of",
    "select_boards",
    "sortie_board_id",
    "sortie_keypoints",
]
