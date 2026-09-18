"""Модель съёмки (E2): из требуемого GSD — в параметры полёта.

Реализовано по описанию заказчика (№26): высота по GSD, углу поля зрения и
разрешению матрицы; ширина захвата; шаг галсов из поперечного перекрытия;
перекрытия из конфига типа съёмки `data/survey_presets.yaml` (№25); путевые
скорости, которые борт удержит при заданном ветре (№45); совместимость
«борт × нагрузка». Не реализовано: учёт нижней границы облачности (№22, P2).
"""

from __future__ import annotations

from core.survey.plan import build_survey_plan
from core.survey.presets import load_presets, resolve_overlaps

__all__ = ["build_survey_plan", "load_presets", "resolve_overlaps"]
