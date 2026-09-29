# CLAUDE.md

Сервис планирования и распределения беспилотных авиационных работ: по сценарию (область съёмки, тип съёмки и GSD, флот БВС, площадки взлёта-посадки, запретные зоны с высотами и временем, высотные препятствия, коридор высот, ветер) делит работу между разнотипными БВС, рассчитывает полётные задания и расписание и выгружает их в KML/GeoJSON. Выход — полный план или отчёт о невыполнимости. Оптимизация — минимум срока или минимум ресурса техники.

## Правило сессии

**Начинать с чтения [`docs/dev/current_state.md`](../docs/dev/current_state.md), заканчивать его обновлением.**

Полные правила разработки — в [`docs/dev/AGENT.md`](../docs/dev/AGENT.md). Прочитать перед первой правкой кода.

**Требования заказчика** — [`docs/dev/customer_qa.md`](../docs/dev/customer_qa.md) (получены 2026-09-18, дополнены 2026-09-24). Документы и каркас кода приведены к ним; что ещё расходится — `docs/dev/current_state.md` §6. При расхождении правы документы.

**Образцы входных данных заказчика** — [`data/samples/`](../data/samples/README.md), правила их чтения — [`docs/input_formats.md`](../docs/input_formats.md).

## Документы

| Файл | Что внутри |
|---|---|
| [`docs/dev/AGENT.md`](../docs/dev/AGENT.md) | Правила разработки, карта репозитория, чек-лист инвариантов, формат отчёта |
| [`docs/dev/PROJECT_BRIEF.md`](../docs/dev/PROJECT_BRIEF.md) | Источник истины по архитектуре, стек, доменная модель, правила предметной области, критерии оптимизации |
| [`docs/dev/customer_qa.md`](../docs/dev/customer_qa.md) | Ответы заказчика по вопросам, значения от заказчика, открытые вопросы. Ссылка «№N» ведёт сюда |
| [`docs/dev/architecture.md`](../docs/dev/architecture.md) | Обоснование решений, черновик алгоритма, порядок разработки, риски |
| [`docs/dev/epics.md`](../docs/dev/epics.md) | Состав работ E0–E15, граф зависимостей, критерии готовности |
| [`docs/dev/current_state.md`](../docs/dev/current_state.md) | **Живой** статус: что работает, что заглушка, журнал изменений |
| [`docs/input_formats.md`](../docs/input_formats.md) | Чтение входных слоёв KML: область, зоны, препятствия. **Предложенный формат высот и времени зон** — требование заказчика (№53) |
| [`docs/export_format.md`](../docs/export_format.md) | Формат выгрузки GeoJSON, KML, расписания CSV — требование заказчика (№21) |
| [`docs/dev/planner_reference.md`](../docs/dev/planner_reference.md) | Что взято из ПО заказчика GeoScan Planner: термины, значения по умолчанию, проверки |
| [`docs/explanatory_note.md`](../docs/explanatory_note.md) | Пояснительная записка для жюри: задача, решение, алгоритм, результаты против baseline |
| [`docs/user_guide.md`](../docs/user_guide.md) | Руководство пользователя: вход, интерфейс, итог, выгрузка, подсказки, история, CLI и API |
| [`docs/known_limitations.md`](../docs/known_limitations.md) | Известные ограничения; раздел реестра собирается `assumptions --update`, тест следит за свежестью |
| [`docs/external_sources.md`](../docs/external_sources.md) | ТТХ БВС, подложка, рельеф, препятствия, спецификации форматов |

## Команды

```bash
# запуск всего (api + web) на чистой машине
docker compose up

# ядро без веба
pip install -e ".[dev]"
python -m core.cli scenarios/simple.json -o out/mission.geojson

# тесты
pytest

# прогон на всех тестовых сценариях
python -m core.cli --batch scenarios/ -o out/

# доказательство эффективности: baseline, эвристика и эвристика с локальным поиском в обоих режимах (E12)
python -m core.cli compare scenarios/ scenarios/real/ -o out/compare.md

# фронт «срок — ресурс»: минимум срока, минимум ресурса и ресурс к срокам между ними
python -m core.cli pareto scenarios/demo_100km2.json -o out/pareto.md

# подбор места ещё одной площадки: кандидаты у дальней работы, каждый проверен расчётом
python -m core.cli sites scenarios/demo_100km2.json -o out/sites.md

# известные ограничения: раздел реестра допущений в документе (после правки assumptions.py)
python -m core.cli assumptions --update docs/known_limitations.md

# подсказки по пулу (флот сценария — пул): срок при N бортах, сколько бортов к сроку, перебор N
python -m core.cli hints scenarios/demo_100km2.json --boards 4
python -m core.cli hints scenarios/demo_100km2.json --deadline-h 6 -o out/hints.md
python -m core.cli hints scenarios/demo_100km2.json --sweep

# сборка сценария из слоёв KML заказчика (docs/input_formats.md)
python -m core.cli import-layers --area data/samples/survey_area_msk.kml \
    --zones data/samples/zones_msk.kml --obstacles data/samples/obstacles_msk.kml \
    --params scenarios/sources/real_msk/params.json -o scenarios/real/real_msk.json

# второй сценарий того же рода: островки заказчика в новой раскладке (отложенный real_nw)
python -m core.cli relayout-area --area data/samples/survey_area_msk.kml \
    --center 36.75,56.07 --size-km 60 58 --seed 1 -o scenarios/sources/real_nw/area.kml

# сценарий комплектом файлов заказчика (слои KML + параметры) и сверка всех сценариев с их файлами
python -m core.cli export-layers scenarios/simple.json -o scenarios/sources/simple
python -m core.cli check-sources

# генерация TypeScript-типов из core/schemas.py (после ЛЮБОЙ правки контракта)
python -m core.cli export-schema -o web/src/types/schema.json
cd web && npm run gen:types   # закреплённая версия генератора из web/package.json
```

## Критичные инварианты

1. **Вся геометрия — в метрической проекции.** WGS84 только на границах: вход сценария и выход экспорта. Ошибка от расчётов в градусах — тихая.
2. **`core` не импортирует `api` и `web`.** Ядро запускается как `python -m core.cli` и живёт без веба.
3. **`core/schemas.py` — общий контракт.** Правка только осознанно, с отметкой в `current_state.md` и перегенерацией TS-типов.
4. **Ветер — метеорологическая конвенция (откуда дует).** Нужен для выполнимости и направления галсов, во время и расход не входит.
5. **Физические константы — только в `core/assumptions.py`**, каждая с источником. ТТХ БВС — из `data/vehicles.yaml` или заданные пользователем в сценарии (`Scenario.vehicles`, поля в `user_fields`); команда их не выдумывает.
6. **Baseline-алгоритм (равномерное деление) не удаляется никогда** — точка сравнения для доказательства эффективности.
7. **Ни один выданный план не нарушает жёстких правил:** запретные зоны, препятствия, коридор высот, разведение бортов. Нарушение — невыполнимость, а не предупреждение. Это главное, что проверяет заказчик.
8. **Частичных планов нет:** область покрыта целиком или результат объявлен невыполнимым с причиной и рекомендацией.

Полный чек-лист из 10 пунктов — [`docs/dev/AGENT.md`](../docs/dev/AGENT.md) §3.
