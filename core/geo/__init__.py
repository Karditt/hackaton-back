"""Геопространственное ядро (E1): проекции и рабочая маска области.

ИНВАРИАНТ 1 (PROJECT_BRIEF.md §5): вся геометрия считается в метрической
проекции. WGS84 пересекает границу системы ровно дважды — здесь, на входе
(`Projector.to_metric`), и в `core.export`, на выходе (`Projector.to_geo`).
Расчёты в градусах дают неверные расстояния и площади, и ошибка тихая.

Состояние: рабочая часть E1 (выбор проекции, перевод координат, вычитание
запретных зон) реализована — без неё конвейер не собирается. Не реализовано:
валидация и починка «грязной» геометрии, высотные слои запретных зон,
сшивка областей на стыке зон UTM. См. TODO(E1) по коду.
"""

from __future__ import annotations

from core.geo.crs import pick_working_crs, utm_epsg
from core.geo.mask import WorkArea, build_work_area
from core.geo.project import Projector

__all__ = ["Projector", "WorkArea", "build_work_area", "pick_working_crs", "utm_epsg"]
