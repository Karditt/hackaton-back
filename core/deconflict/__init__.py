"""Расписание и разведение бортов (E14, P0).

Заказчик проверяет решение прежде всего краш-тестом: нет столкновений и
опасных сближений (№4); расписание — обязательный выход (№31).

* `levels` — эшелоны перелёта внутри коридора высот (№10): разведение по
  вертикали.
* `schedule` — вылеты на общей оси времени; старт сдвигается, пока есть
  сближения (№37) или пересечение временной зоны в её окне (№40).

Состояние: ЗАГЛУШКА в части поиска — жадные сдвиги старта. TODO(E14) по коду.
"""

from __future__ import annotations

from core.deconflict.levels import assign_transit_levels
from core.deconflict.schedule import PreparedSortie, ScheduleBook, build_schedule

__all__ = ["PreparedSortie", "ScheduleBook", "assign_transit_levels", "build_schedule"]
