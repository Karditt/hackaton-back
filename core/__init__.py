"""Ядро сервиса планирования беспилотных авиационных работ.

Чистый Python, ноль веб-зависимостей. Запускается без API и без веба:

    python -m core.cli scenarios/simple.json -o out/mission.geojson

Инвариант 2 (docs/dev/PROJECT_BRIEF.md §5): `core` не импортирует ничего из `api` и `web`.
Любой такой импорт — дефект, даже «только для типов».

Конвейер стадий (по ответам заказчика, docs/dev/customer_qa.md):

    Scenario → SurveyPlan → Decomposition → Allocation → Coverage
             → вылеты → Schedule → Validation → Mission → Files

Итог — полный план или отчёт о невыполнимости; частичных планов нет.

Сборка конвейера — в `core.pipeline`, общий контракт — в `core.schemas`.
"""

from __future__ import annotations

__version__ = "0.1.0"
