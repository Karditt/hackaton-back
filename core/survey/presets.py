"""Перекрытия по типам съёмки из `data/survey_presets.yaml`.

Отдельный конфиг потребовал заказчик (№25). Загрузчик не подставляет ничего
от себя: нет записи или нет источника у числа — это ошибка конфига.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from core.errors import CatalogError, ScenarioError
from core.schemas import QualityRequirement, SurveyType

DEFAULT_PRESETS_PATH = Path(__file__).resolve().parents[2] / "data" / "survey_presets.yaml"

#: Переменная окружения для подмены конфига (тесты, Docker).
PRESETS_ENV_VAR = "GEOSCAN_SURVEY_PRESETS"

_FIELDS = ("forward_overlap", "side_overlap", "min_side_overlap")


@dataclass(frozen=True)
class SurveyPreset:
    """Перекрытия одного типа съёмки, доли 0..1, с источником каждого числа."""

    survey_type: SurveyType
    forward_overlap: float
    side_overlap: float
    min_side_overlap: float
    sources: dict[str, str]


def _value(raw: Any, survey_type: str, field: str) -> tuple[float, str]:
    """Достать число и его источник из записи конфига."""
    if not isinstance(raw, dict) or "value" not in raw:
        raise CatalogError(f"survey_presets: у {survey_type}.{field} нет значения")
    source = str(raw.get("source") or "").strip()
    if not source:
        raise CatalogError(f"survey_presets: у {survey_type}.{field} нет источника (инвариант 5)")
    value = float(raw["value"])
    if not 0.0 <= value < 1.0:
        raise CatalogError(f"survey_presets: {survey_type}.{field} = {value} вне [0, 1)")
    return value, source


@lru_cache(maxsize=4)
def load_presets(path: str | None = None) -> dict[SurveyType, SurveyPreset]:
    """Прочитать конфиг перекрытий.

    Args:
        path: путь к YAML. None — переменная окружения, затем файл по умолчанию.

    Returns:
        Пресеты по типам съёмки.

    Raises:
        CatalogError: файла нет, он повреждён или в нём нет записи на какой-то тип.
    """
    resolved = Path(path or os.environ.get(PRESETS_ENV_VAR) or DEFAULT_PRESETS_PATH)
    if not resolved.is_file():
        raise CatalogError(f"Конфиг перекрытий не найден: {resolved}")
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("presets"), dict):
        raise CatalogError(f"В конфиге {resolved} нет корневого ключа 'presets'")

    presets: dict[SurveyType, SurveyPreset] = {}
    for survey_type in SurveyType:
        entry = raw["presets"].get(survey_type.value)
        if not isinstance(entry, dict):
            raise CatalogError(f"survey_presets: нет записи для типа {survey_type.value}")
        values: dict[str, float] = {}
        sources: dict[str, str] = {}
        for field in _FIELDS:
            values[field], sources[field] = _value(entry.get(field), survey_type.value, field)
        presets[survey_type] = SurveyPreset(survey_type=survey_type, sources=sources, **values)
    return presets


def resolve_overlaps(
    survey_type: SurveyType, quality: QualityRequirement
) -> tuple[float, float, SurveyPreset]:
    """Перекрытия для расчёта: из сценария, а если не заданы — из конфига.

    Args:
        survey_type: тип съёмки.
        quality: требования сценария.

    Returns:
        Тройка «продольное, поперечное, пресет типа».

    Raises:
        ScenarioError: поперечное перекрытие сценария ниже минимума для типа
            съёмки — снимки не сошьются (заказчик, №25).
    """
    preset = load_presets()[survey_type]
    forward = preset.forward_overlap if quality.forward_overlap is None else quality.forward_overlap
    side = preset.side_overlap if quality.side_overlap is None else quality.side_overlap
    if side < preset.min_side_overlap:
        raise ScenarioError(
            f"Поперечное перекрытие {side * 100:.0f}% ниже минимума "
            f"{preset.min_side_overlap * 100:.0f}% для съёмки типа {survey_type.value}: "
            f"общая карта не сошьётся ({preset.sources['min_side_overlap']})"
        )
    return forward, side, preset


__all__ = [
    "DEFAULT_PRESETS_PATH",
    "PRESETS_ENV_VAR",
    "SurveyPreset",
    "load_presets",
    "resolve_overlaps",
]
