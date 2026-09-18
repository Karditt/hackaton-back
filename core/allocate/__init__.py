"""Распределение по флоту и оптимизация (E6).

Состояние: реализован ШАГ 1 из трёх — baseline. Единица распределения —
подобласть, а не галс (заказчик, №27, №46). Шаги 2 и 3 (конструктивная
эвристика и локальный поиск) не начаты, их закрывает E6.

ИНВАРИАНТ 8 (PROJECT_BRIEF.md §5): baseline не удаляется, не «рефакторится в
ноль» и остаётся доступным всегда. Он — точка сравнения, которой измеряется
эффективность решения (E12).

Критерии — два режима, «минимум срока» и «минимум ресурса при дедлайне»
(PROJECT_BRIEF.md §4.3), живут в `core.allocate.objective`. Менять их без
явной задачи запрещено (AGENT.md §7).
"""

from __future__ import annotations

from core.allocate.baseline import build_baseline_allocation
from core.allocate.fleet import FleetInstance, expand_fleet
from core.allocate.objective import objective_value, resource_s, weighted_value

__all__ = [
    "FleetInstance",
    "build_baseline_allocation",
    "expand_fleet",
    "objective_value",
    "resource_s",
    "weighted_value",
]
