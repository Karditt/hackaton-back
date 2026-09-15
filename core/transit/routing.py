"""Маршрутизация перелётов между точками.

ЗАГЛУШКА E5. Соединяет точки прямой и, если прямая рассекает запретную зону,
выдаёт предупреждение с геометрией нарушенного участка — так проблема видна
на карте, а не маскируется. Настоящий E5 строит граф видимости по границам
зон и возвращает кратчайший обход.
"""

from __future__ import annotations

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from core.schemas import MetricLineString, MetricPoint, PlanWarning, Severity, Stage


class StraightLineRouter:
    """Прямолинейный маршрутизатор с проверкой на пересечение запретных зон."""

    def __init__(self, no_fly: BaseGeometry | None = None) -> None:
        self._no_fly = no_fly

    def __call__(
        self, start: MetricPoint, end: MetricPoint
    ) -> tuple[MetricLineString, list[PlanWarning]]:
        """Проложить перелёт.

        Args:
            start: точка начала, метры рабочей проекции.
            end: точка конца, метры рабочей проекции.

        Returns:
            Пара «ломаная маршрута, предупреждения». Ломаная всегда состоит
            ровно из двух точек: обход зон не реализован.
        """
        line = MetricLineString(points=[start, end])
        warnings: list[PlanWarning] = []

        if self._no_fly is not None and not self._no_fly.is_empty:
            segment = LineString([(start.x_m, start.y_m), (end.x_m, end.y_m)])
            if segment.intersects(self._no_fly):
                # TODO(E5): здесь должен строиться обход по графу видимости.
                warnings.append(
                    PlanWarning(
                        code="transit.crosses_nfz",
                        severity=Severity.ERROR,
                        stage=Stage.TRANSIT,
                        message=(
                            "Транзитный перелёт проходит сквозь запретную зону: обход "
                            "не реализован (TODO(E5), граф видимости). Участок требует "
                            "ручной проверки перед вылетом"
                        ),
                        geometry=line,
                    )
                )
        return line, warnings


def make_router(no_fly: BaseGeometry | None = None) -> StraightLineRouter:
    """Создать маршрутизатор перелётов для рабочей области."""
    return StraightLineRouter(no_fly)


__all__ = ["StraightLineRouter", "make_router"]
