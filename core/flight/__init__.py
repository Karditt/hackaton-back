"""Модель полёта (E4): фазы, время, расход, выполнимость, 4D-траектория.

По ответам заказчика модель упрощена, а не усложнена:

* `core.flight.wind` — ИНВАРИАНТ 4 (метеоконвенция) и треугольник скоростей.
  Ветер определяет выполнимость — хватит ли воздушной скорости, чтобы держать
  заданную путевую (№45), — и не входит во время и расход (№36).
* `core.flight.cost` — вылет по фазам; на галсе и перелёте время — длина,
  делённая на путевую скорость, которую держит автопилот; расход линеен по
  времени, отсечка 20% (№34, №35); эшелон перелёта; разные площадки взлёта и
  посадки (№29). Разворот самолёта — время по радиусу, петля не строится (№38).
* `core.flight.track` — 4D-траектория и поиск сближений для расписания и
  валидатора (№4, №37).

Не реализовано: круг ожидания в воздухе (разведение сейчас — сдвигом старта
на земле), маршрут захода на посадку самолёта (№30, P2).
"""

from __future__ import annotations

from core.flight.cost import build_sortie, retime_sortie, sortie_is_feasible
from core.flight.track import build_track, first_conflict
from core.flight.wind import (
    crab_angle_deg,
    crosswind_mps,
    ground_speed_mps,
    required_airspeed_mps,
    wind_vector,
)

__all__ = [
    "build_sortie",
    "build_track",
    "crab_angle_deg",
    "crosswind_mps",
    "first_conflict",
    "ground_speed_mps",
    "required_airspeed_mps",
    "retime_sortie",
    "sortie_is_feasible",
    "wind_vector",
]
