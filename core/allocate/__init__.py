"""Распределение по флоту и оптимизация (E6).

Состояние: реализован ШАГ 1 из трёх — baseline. Шаги 2 и 3 (конструктивная
эвристика и локальный поиск) не начаты, их закрывает E6.

ИНВАРИАНТ 8 (PROJECT_BRIEF.md §5): baseline не удаляется, не «рефакторится в
ноль» и остаётся доступным всегда. Он — точка сравнения, которой измеряется
эффективность решения (E12). Без него выигрыш оптимизатора нечем показать.

Целевая функция `α·makespan + (1−α)·суммарный налёт` зафиксирована и живёт в
`core.allocate.objective`. Менять её вес, слагаемые или нормировку без явной
задачи запрещено (AGENT.md §7).
"""

from __future__ import annotations

from core.allocate.baseline import build_baseline_allocation
from core.allocate.objective import objective_value

__all__ = ["build_baseline_allocation", "objective_value"]
