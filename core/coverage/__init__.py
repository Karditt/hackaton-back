"""Декомпозиция и покрытие (E3).

Порядок по ответам заказчика (№27, №37, №46): маска съёмки делится на
подобласти (`decompose`), подобласти распределяются по вылетам (E6), и только
потом каждый борт строит в своей подобласти галсы со своим шагом
(`CoverageOracle`, `cover_cell`). У самолётного типа на концах галсов —
защитные зоны манёвра (№38).

Состояние: ЗАГЛУШКА. Подобласти — сетка квадратов; направление галсов одно
на область и берётся по длинной стороне, а не перебором с учётом ветра.
TODO(E3) по коду.
"""

from __future__ import annotations

from core.coverage.decompose import cell_size_m, decompose
from core.coverage.oracle import CoverageOracle
from core.coverage.strips import cover_cell, dominant_azimuth_deg, maneuver_zones

__all__ = [
    "CoverageOracle",
    "cell_size_m",
    "cover_cell",
    "decompose",
    "dominant_azimuth_deg",
    "maneuver_zones",
]
