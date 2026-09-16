# Типы контракта

`schema.json` порождается из `core/schemas.py` и **коммитится**: он нужен тем,
у кого под рукой нет Python. `schema.ts` порождается из него и в репозиторий
не попадает.

Руками не правится ни то, ни другое (AGENT.md §2: дублирование схем запрещено).

После ЛЮБОЙ правки `core/schemas.py`:

```bash
python -m core.cli export-schema -o web/src/types/schema.json
npm run gen:types
```

и отметка в `docs/current_state.md` → «Журнал изменений» о правке контракта
(AGENT.md §4).
