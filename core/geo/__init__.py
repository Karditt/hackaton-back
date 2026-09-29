"""Геопространственное ядро (E1): проекции, валидация, зоны, препятствия, маска.

ИНВАРИАНТ 1 (docs/dev/PROJECT_BRIEF.md §5): вся геометрия считается в метрической
проекции. WGS84 пересекает границу системы здесь, на входе
(`Projector.to_metric`), и в `core.export`, на выходе (`Projector.to_geo`).
Расчёты в градусах дают неверные расстояния и площади, и ошибка тихая.

Реализовано: выбор проекции, перевод координат, мультиполигон области,
отказ от некорректной геометрии с указанием места (заказчик, №9, №19),
запретные зоны с высотами и окнами действия (№40), слой высотных
препятствий (№2), коридор высот (№10), маска съёмки. Не реализовано: ЦМР
(P1), сшивка областей на стыке зон UTM. См. TODO(E1) по коду.
"""

from __future__ import annotations

from core.geo.crs import pick_working_crs, utm_epsg
from core.geo.mask import ObstacleArea, WorkArea, ZoneArea, build_survey_mask, build_work_area
from core.geo.project import Projector

__all__ = [
    "ObstacleArea",
    "Projector",
    "WorkArea",
    "ZoneArea",
    "build_survey_mask",
    "build_work_area",
    "pick_working_crs",
    "utm_epsg",
]
