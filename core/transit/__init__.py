"""Транзитные перелёты с обходом запретных зон (E5).

Состояние: ЗАГЛУШКА ровно в том виде, который E5 и предписывает как
промежуточный, — прямая линия с предупреждением при пересечении запретной
зоны. Графа видимости нет. TODO(E5) по коду.
"""

from __future__ import annotations

from core.transit.routing import StraightLineRouter, make_router

__all__ = ["StraightLineRouter", "make_router"]
