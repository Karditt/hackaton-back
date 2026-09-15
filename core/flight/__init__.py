"""Модель полёта (E4): ветер, время и энергия по фазам, выполнимость вылета.

Состояние смешанное, и это сделано намеренно:

* `core.flight.wind` — РЕАЛИЗОВАНО. Здесь живёт инвариант 4 (метеорологическая
  конвенция направления ветра) и треугольник скоростей. Ошибка знака в этом
  месте даёт зеркальный план, поэтому кинематика написана сразу и покрыта
  тестами.
* `core.flight.cost` — ЗАГЛУШКА. Фазы полёта разделены, ветер в путевой
  скорости учтён, энергия считается через среднюю мощность из ёмкости АКБ.
  Не реализовано: настоящая геометрия разворота, схема «через галс» при
  узком шаге, зависимость расхода от массы и высоты, снос на посадке.
  TODO(E4) по коду.
"""

from __future__ import annotations

from core.flight.cost import build_sortie, estimate_sortie_duration_s, sortie_is_feasible
from core.flight.wind import crab_angle_deg, crosswind_mps, ground_speed_mps, wind_vector

__all__ = [
    "build_sortie",
    "crab_angle_deg",
    "crosswind_mps",
    "estimate_sortie_duration_s",
    "ground_speed_mps",
    "sortie_is_feasible",
    "wind_vector",
]
