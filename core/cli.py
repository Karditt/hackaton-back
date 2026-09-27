"""Командная строка ядра: прогон конвейера и служебные команды.

ИНВАРИАНТ 2: ядро запускается без API и без веба. Если `api/` удалить из
репозитория целиком, всё, что здесь описано, обязано продолжать работать.

    python -m core.cli scenarios/simple.json -o out/mission.geojson
    python -m core.cli scenarios/simple.json -o out/gemini1.kml --board geoscan-gemini_1
    python -m core.cli --batch scenarios/ -o out/
    python -m core.cli compare scenarios/ scenarios/real/ -o out/compare.md
    python -m core.cli hints scenarios/demo_100km2.json --boards 4
    python -m core.cli hints scenarios/demo_100km2.json --deadline-h 6 -o out/hints.md
    python -m core.cli hints scenarios/demo_100km2.json --sweep
    python -m core.cli import-layers --area area.kml --zones zones.kml \
        --obstacles obstacles.kml --params params.json -o scenarios/real.json
    python -m core.cli export-layers scenarios/simple.json -o scenarios/sources/simple
    python -m core.cli relayout-area --area data/samples/survey_area_msk.kml \
        --center 36.75,56.07 --size-km 60 58 --seed 1 -o scenarios/sources/real_nw/area.kml
    python -m core.cli check-sources
    python -m core.cli export-schema -o web/src/types/schema.json
    python -m core.cli assumptions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from core import assumptions as assumptions_module
from core.catalog import load_catalog
from core.errors import CoreError
from core.export.input_layers import export_input_layers, multipolygon_kml
from core.export.package import write_mission_files
from core.export.writer import export_mission, format_for
from core.geo.kml_import import import_layers, read_area
from core.geo.relayout import DEFAULT_LINK_M, relayout_area
from core.hints import boards_text, duration_text, run_hint
from core.pipeline import ALLOCATORS, PlanningRun, run_pipeline
from core.schemas import (
    GeoPoint,
    HintKind,
    HintReport,
    HintRun,
    ImportReport,
    OptimizationMode,
    PipelineResult,
    PlanWarning,
    Scenario,
    Severity,
    Vehicle,
)
from core.sources import build_from_sources, load_sources, scenario_differences

#: Подкоманды, которые не являются путём к сценарию.
SUBCOMMANDS = (
    "plan",
    "import-layers",
    "export-layers",
    "compare",
    "hints",
    "relayout-area",
    "check-sources",
    "export-schema",
    "assumptions",
)


def load_scenario(path: Path) -> Scenario:
    """Прочитать сценарий из JSON.

    Args:
        path: путь к файлу сценария.

    Returns:
        Разобранный сценарий.

    Raises:
        CoreError: файла нет либо он не соответствует контракту.
    """
    if not path.is_file():
        raise CoreError(f"Сценарий не найден: {path}")
    try:
        return Scenario.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise CoreError(f"Сценарий {path.name} не соответствует контракту:\n{error}") from error


def _print_grouped(label: str, warnings: list[PlanWarning], limit: int = 8) -> None:
    """Напечатать предупреждения, схлопнув повторы одного кода.

    Один нереализованный обход запретной зоны даёт по предупреждению на каждый
    транзитный участок. Повторы не добавляют информации, но прячут остальные
    строки отчёта, поэтому одинаковые коды выводятся одной строкой со счётчиком.
    """
    grouped: dict[tuple[str, str], list[PlanWarning]] = {}
    for warning in warnings:
        grouped.setdefault((warning.code, warning.message), []).append(warning)

    for index, ((_, message), items) in enumerate(grouped.items()):
        if index >= limit:
            print(f"    ... и ещё {len(grouped) - limit} различных сообщений")
            break
        repeats = f" (x{len(items)})" if len(items) > 1 else ""
        print(f"    [{label} {items[0].stage.value}]{repeats} {message}")


def _print_report(run: PlanningRun) -> None:
    """Напечатать краткий отчёт о прогоне."""
    mission = run.mission
    print(f"  статус          : {'ВЫПОЛНИМ' if mission.is_feasible() else 'НЕВЫПОЛНИМ'}")
    print(f"  режим           : {mission.mode.value} (α={mission.alpha}, seed={mission.seed})")
    print(f"  алгоритм        : {mission.algorithm}")
    print(f"  проекция        : EPSG:{mission.crs.epsg} — {mission.crs.name}")

    metrics = mission.metrics
    if metrics is not None:
        print(f"  подобластей     : {metrics.cells_covered} из {metrics.cells_planned}")
        print(f"  вылетов / бортов: {metrics.sortie_count} / {metrics.vehicle_count}")
        print(f"  срок (makespan) : {metrics.makespan_s / 60:.1f} мин")
        print(f"  налёт           : {metrics.total_flight_time_s / 60:.1f} мин")
        print(f"  ресурс          : {metrics.resource_s / 60:.1f} мин (налёт + циклы)")
        print(f"  дистанция       : {metrics.total_distance_m / 1000:.1f} км")
        if metrics.deadline_s is not None:
            state = "выполнено" if metrics.deadline_met else "НЕ выполнено"
            print(f"  дедлайн         : {metrics.deadline_s / 3600:.1f} ч — {state}")

    if mission.reasons:
        print(f"  причин          : {len(mission.reasons)}")
        _print_grouped("ПРИЧИНА ", mission.reasons)
    for item in mission.recommendations:
        print(f"    [СОВЕТ {item.kind.value}] {item.message}")

    # PipelineResult.warnings() уже включает предупреждения миссии.
    warnings = run.result.warnings()
    alerts = [item for item in warnings if item.severity is Severity.WARNING]
    print(f"  предупреждений  : {len(alerts)}")
    _print_grouped("ВНИМАНИЕ", alerts)

    if mission.is_stub():
        stages = ", ".join(stage.value for stage in mission.stub_stages)
        print(f"  ЗАГЛУШКИ        : {stages} — план не является окончательным расчётом")


def _write_outputs(run: PlanningRun, target: Path, batch: bool, board: str | None) -> list[Path]:
    """Выгрузить итог в файлы.

    В пакетном режиме и при указании каталога пишется весь комплект:
    GeoJSON для фронта, KML для стороннего ПО, расписание в CSV и KML с
    заданием каждого борта (docs/export_format.md §1). Путь к файлу — один
    файл по расширению; с `board` — задание одного борта.
    """
    projector = run.work_area.projector
    directory_mode = batch or target.suffix == ""
    if directory_mode:
        return write_mission_files(run.result, run.scenario, projector, target)
    return [export_mission(run.result, run.scenario, projector, target, format_for(target), board)]


def _dump_stages(run: PlanningRun, directory: Path) -> Path:
    """Сохранить все промежуточные стадии конвейера одним JSON."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run.scenario.id}.stages.json"
    payload: PipelineResult = run.result
    path.write_text(payload.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")
    return path


def command_plan(args: argparse.Namespace) -> int:
    """Прогнать конвейер на одном сценарии или на каталоге сценариев."""
    catalog = load_catalog(args.vehicles)
    target = Path(args.output)

    if args.batch:
        directory = Path(args.batch)
        scenarios = sorted(path for path in directory.glob("*.json") if path.is_file())
        if not scenarios:
            print(f"В каталоге {directory} нет сценариев (*.json)", file=sys.stderr)
            return 2
    else:
        scenarios = [Path(args.scenario)]

    failures = 0
    for path in scenarios:
        print(f"\n=== {path.name} ===")
        try:
            scenario = load_scenario(path)
            mode = OptimizationMode(args.mode) if args.mode else None
            run = run_pipeline(
                scenario,
                catalog=catalog,
                mode=mode,
                alpha=args.alpha,
                seed=args.seed,
                allocator=args.allocator,
            )
        except CoreError as error:
            failures += 1
            print(f"  ПРОВАЛ: {error}", file=sys.stderr)
            continue

        _print_report(run)
        for written in _write_outputs(run, target, bool(args.batch), args.board):
            print(f"  записано        : {written}")
        if args.dump_stages:
            print(f"  стадии          : {_dump_stages(run, Path(args.dump_stages))}")

    return 1 if failures else 0


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.2f}".replace(".", ",")


def _change(new: float, old: float) -> str:
    return f"{(new / old - 1.0) * 100:+.1f}%".replace(".", ",") if old > 0 else "—"


def command_compare(args: argparse.Namespace) -> int:
    """Сравнить распределения на сценариях: baseline против оптимизатора (E12).

    Три алгоритма — baseline, эвристика без поиска и эвристика с локальным
    поиском — считаются на одних и тех же подобластях и одной модели полёта,
    в обоих режимах критерия. Итог — таблица Markdown: срок и ресурс каждого,
    изменение у оптимизатора (эвристика с поиском) против baseline в
    процентах, выполнимость. Краш-тест проходит каждый план, как в обычном
    прогоне.
    """
    catalog = load_catalog(args.vehicles)
    paths: list[Path] = []
    for item in args.scenarios:
        path = Path(item)
        if not path.is_dir():
            paths.append(path)
            continue
        # Рядом со сценариями на данных заказчика лежат отчёты импорта — не сценарии.
        paths.extend(
            found
            for found in sorted(path.glob("*.json"))
            if not found.name.endswith(".import.json")
        )
    modes = [OptimizationMode(args.mode)] if args.mode else [mode for mode in OptimizationMode]
    lines = [
        "| Сценарий | Режим | Baseline: срок, ч | Baseline: ресурс, ч "
        "| Эвристика: срок, ч | Эвристика: ресурс, ч "
        "| С поиском: срок, ч | С поиском: ресурс, ч | Δ срока | Δ ресурса |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for path in paths:
        scenario = load_scenario(path)
        for mode in modes:
            cells: list[str] = []
            metrics = {}
            for allocator in ("baseline", "constructive", "local_search"):
                run = run_pipeline(scenario, catalog=catalog, mode=mode, allocator=allocator)
                metrics[allocator] = run.mission.metrics
                if run.mission.metrics is None:
                    cells.extend(["невыполним", "—"])
                else:
                    cells.extend(
                        [
                            _hours(run.mission.metrics.makespan_s),
                            _hours(run.mission.metrics.resource_s),
                        ]
                    )
            old, new = metrics["baseline"], metrics["local_search"]
            if old is not None and new is not None:
                cells.extend(
                    [
                        _change(new.makespan_s, old.makespan_s),
                        _change(new.resource_s, old.resource_s),
                    ]
                )
            else:
                cells.extend(["—", "—"])
            line = f"| `{scenario.id}` | {mode.value} | " + " | ".join(cells) + " |"
            lines.append(line)
            print(line, flush=True)
    table = "\n".join(lines) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(table, encoding="utf-8")
        print(f"\nзаписано: {target}")
    return 0


def _hint_row(run: HintRun) -> list[str]:
    """Ячейки строки таблицы подсказки: N, состав, итог, срок, ресурс, вылетов."""
    composition = ", ".join(f"{unit.count} × {unit.vehicle_id}" for unit in run.fleet)
    if run.makespan_s is None:
        outcome = "невыполним"
    elif run.meets_deadline is False:
        outcome = "не успевает"
    else:
        outcome = "выполним"
    return [
        str(run.boards),
        composition,
        outcome,
        _hours(run.makespan_s) if run.makespan_s is not None else "—",
        _hours(run.resource_s) if run.resource_s is not None else "—",
        str(run.sortie_count) if run.sortie_count is not None else "—",
    ]


def _hint_markdown(report: HintReport) -> str:
    """Отчёт подсказки таблицей Markdown."""
    lines = [
        f"**{report.answer}**",
        "",
        "| N | Состав | Итог | Срок, ч | Ресурс, ч | Вылетов | Как подобран состав |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in report.runs:
        lines.append("| " + " | ".join([*_hint_row(run), run.note or "по порядку пула"]) + " |")
    return "\n".join(lines) + "\n"


def command_hints(args: argparse.Namespace) -> int:
    """Подсказки по пулу: срок при N бортах, сколько бортов к сроку (заказчик, №51).

    Флот сценария — пул; на каждое N прогоняется обычный конвейер, поэтому
    ответ подтверждён настоящим планом. Таблица печатается по мере прогонов:
    на данных заказчика прогон — десятки секунд.
    """
    if args.boards is not None:
        kind = HintKind.BOARDS
    elif args.sweep:
        kind = HintKind.SWEEP
    elif args.deadline_h is not None:
        kind = HintKind.DEADLINE
    else:
        print("Укажите вопрос: --boards N, --deadline-h ЧАСЫ или --sweep", file=sys.stderr)
        return 2
    deadline_s = args.deadline_h * 3600.0 if args.deadline_h is not None else None
    scenario = load_scenario(Path(args.scenario))
    catalog = load_catalog(args.vehicles)

    question = {
        HintKind.BOARDS: f"срок при N = {args.boards}",
        HintKind.DEADLINE: f"сколько бортов к сроку {duration_text(deadline_s or 0.0)}",
        HintKind.SWEEP: "срок при каждом числе бортов",
    }[kind]
    print(f"=== {scenario.id}: {question}")
    print("  N | состав | итог | срок, ч | ресурс, ч | вылетов | расчёт")

    def on_run(run: HintRun) -> None:
        print(f"  {' | '.join(_hint_row(run))} | {run.elapsed_s:.0f} с", flush=True)
        if run.note:
            print(f"      состав : {run.note}", flush=True)
        if run.reasons:
            print(f"      причина: {run.reasons[0].message}", flush=True)

    try:
        result = run_hint(
            scenario, kind, args.boards, deadline_s, catalog, seed=args.seed, on_run=on_run
        )
    except ValueError as error:
        raise CoreError(str(error)) from error
    report = result.report
    groups: list[list[str | int]] = []
    for board in report.pool:
        label = f"{board.vehicle_name} ({board.productivity_m2ps:.0f} м²/с)"
        if board.site_id:
            label += f", площадка {board.site_id}"
        if groups and groups[-1][1] == label:
            groups[-1][0] = int(groups[-1][0]) + 1
        else:
            groups.append([1, label])
    print(f"  пул            : {boards_text(len(report.pool))} по убыванию производительности")
    for count, label in groups:
        print(f"                   {count} × {label}")
    _print_grouped("ВНИМАНИЕ", report.warnings)
    print(f"  ОТВЕТ: {report.answer}")

    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".json":
            target.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        else:
            target.write_text(_hint_markdown(report), encoding="utf-8")
        print(f"  записано       : {target}")
    if args.plan_out and result.plan is not None:
        for written in _write_outputs(result.plan, Path(args.plan_out), True, None):
            print(f"  план ответа    : {written}")
    return 0 if report.answer_boards is not None else 1


def command_import_layers(args: argparse.Namespace) -> int:
    """Собрать сценарий из слоёв KML заказчика и файла параметров (E1, E9).

    Геометрия приходит файлами, всё остальное — параметрами: заказчик рисует
    область в ГИС и не правит её у нас (№8, №12). Отчёт импорта печатается и
    кладётся рядом со сценарием: что починено и что выброшено, должно быть
    видно до расчёта, а не после.
    """
    params_path = Path(args.params)
    if not params_path.is_file():
        raise CoreError(f"Файл параметров не найден: {params_path}")
    payload = json.loads(params_path.read_text(encoding="utf-8"))

    layers = import_layers(
        area_source=Path(args.area),
        zones_source=Path(args.zones) if args.zones else None,
        obstacles_source=Path(args.obstacles) if args.obstacles else None,
        terrain_min_m=args.terrain_min,
        terrain_max_m=args.terrain_max,
    )
    payload["area"] = layers.area.model_dump(mode="json")
    payload["no_fly_zones"] = [zone.model_dump(mode="json") for zone in layers.zones]
    payload["obstacles"] = [obstacle.model_dump(mode="json") for obstacle in layers.obstacles]
    try:
        scenario = Scenario.model_validate(payload)
    except ValidationError as error:
        raise CoreError(f"Собранный сценарий не соответствует контракту:\n{error}") from error

    report = layers.report
    print(f"=== импорт слоёв → {scenario.id}")
    for label, stats in (
        ("область", report.area),
        ("зоны", report.zones),
        ("препятствия", report.obstacles),
    ):
        print(
            f"  {label:<12}: прочитано {stats.read}, принято {stats.kept}, "
            f"исправлено {stats.fixed}, выброшено {stats.dropped}, "
            f"вне области {stats.skipped}"
        )
    print(f"  площадь     : {report.area_km2:.1f} км²")
    _print_grouped("ИМПОРТ", report.warnings, limit=20)

    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(scenario.model_dump(mode="json", exclude_none=True), ensure_ascii=False),
        encoding="utf-8",
    )
    report_path = target.with_suffix(".import.json")
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  записано    : {target}")
    print(f"  отчёт       : {report_path}")
    return 0


def command_export_layers(args: argparse.Namespace) -> int:
    """Выгрузить сценарий комплектом файлов в формате входа заказчика.

    Три слоя KML — область, зоны в предложенном формате, препятствия — и
    параметры задания JSON (docs/input_formats.md). Обратная сторона
    `import-layers`: собранный обратно сценарий — тот же самый.
    """
    scenario = load_scenario(Path(args.scenario))
    written = export_input_layers(scenario, Path(args.output))
    print(f"=== {scenario.id} → комплект входных файлов")
    for kind, path in written.items():
        print(f"  {kind:<10}: {path}")
    return 0


def command_relayout_area(args: argparse.Namespace) -> int:
    """Разложить островки настоящей области по новому месту (core/geo/relayout.py).

    Формы и размеры островков — настоящие, группы соседних переносятся
    целиком; меняется общая раскладка. Так получаются сценарии того же рода,
    что у заказчика, для отложенной проверки алгоритма.
    """
    try:
        lon_text, lat_text = args.center.split(",")
        center = GeoPoint(lon=float(lon_text), lat=float(lat_text))
    except ValueError as error:
        raise CoreError(f"Центр задаётся как «долгота,широта»: {args.center!r}") from error
    area, _ = read_area(Path(args.area))
    width_km, height_km = args.size_km
    result, report = relayout_area(
        area,
        center,
        width_km * 1000.0,
        height_km * 1000.0,
        seed=args.seed,
        link_m=args.link_m,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    title = args.title or f"Область съёмки: островки {Path(args.area).name}, раскладка {args.seed}"
    target.write_bytes(multipolygon_kml(result, title))
    print(f"=== {Path(args.area).name} → {target}")
    print(f"  островков   : {report.pieces}, групп {report.groups} (соседство {args.link_m:g} м)")
    print(f"  площадь     : {report.area_km2:.1f} км²")
    print(f"  прямоугольник: {width_km:g} × {height_km:g} км вокруг {args.center}")
    print(f"  попыток     : {report.attempts}")
    return 0


def command_check_sources(args: argparse.Namespace) -> int:
    """Проверить, что каждый сценарий манифеста собирается из своих файлов.

    Сравнение смысловое: координаты с допуском на шум перевода в метры и
    обратно. Расхождение — повод собрать сценарий заново командой
    `import-layers`, а не править JSON руками.
    """
    sources = load_sources(Path(args.manifest) if args.manifest else None)
    failed = 0
    for scenario_id, item in sources.items():
        built, _ = build_from_sources(item)
        differences = scenario_differences(built, load_scenario(item.scenario))
        status = "совпадает" if not differences else f"РАСХОДИТСЯ ({len(differences)})"
        print(f"  {scenario_id:<14}: {status}")
        for line in differences[:10]:
            print(f"      {line}")
        failed += bool(differences)
    return 1 if failed else 0


def command_export_schema(args: argparse.Namespace) -> int:
    """Выгрузить JSON Schema контракта для генерации TypeScript-типов.

    AGENT.md §4: после ЛЮБОЙ правки `core/schemas.py` эта команда прогоняется
    заново, иначе типы фронта расходятся с ядром молча.

    Корни выгрузки — то, что фронт получает от API целиком: сценарий
    (`GET /scenarios/{id}`), результат прогона (`GET /jobs/{id}/result`),
    борт каталога (`GET /vehicles`), отчёт импорта слоёв
    (`POST /scenarios/import`) и отчёт подсказки (`GET /hints/{id}`).
    Последние три не достижимы из первых, поэтому названы отдельно: иначе их
    типы пришлось бы писать руками, что запрещено (AGENT.md §2).
    """
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "GeoscanPlannerContract",
        "description": "Контракт core/schemas.py. Файл генерируется, руками не правится.",
        "type": "object",
        "properties": {
            "scenario": {"$ref": "#/definitions/Scenario"},
            "pipelineResult": {"$ref": "#/definitions/PipelineResult"},
            "vehicle": {"$ref": "#/definitions/Vehicle"},
            "importReport": {"$ref": "#/definitions/ImportReport"},
            "hintReport": {"$ref": "#/definitions/HintReport"},
        },
        "definitions": {},
    }

    for model in (Scenario, PipelineResult, Vehicle, ImportReport, HintReport):
        model_schema = model.model_json_schema(ref_template="#/definitions/{model}")
        schema["definitions"].update(model_schema.pop("$defs", {}))
        schema["definitions"][model.__name__] = model_schema

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON Schema записана: {path}")
    print("Дальше: cd web && npm run gen:types")
    return 0


def command_assumptions(args: argparse.Namespace) -> int:
    """Напечатать реестр допущений — основу раздела «известные ограничения».

    Знак «!» — допущение команды, «*» — значение заказчика.
    """
    items = (
        assumptions_module.team_assumptions()
        if args.team_only
        else assumptions_module.all_assumptions()
    )
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": item.name,
                        "value": item.value,
                        "unit": item.unit,
                        "kind": item.kind.value,
                        "source": item.source,
                        "rationale": item.rationale,
                    }
                    for item in items
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    markers = {
        assumptions_module.Kind.TEAM: "!",
        assumptions_module.Kind.CUSTOMER: "*",
    }
    for item in items:
        marker = markers.get(item.kind, " ")
        print(f"{marker} {item.name} = {item.value} [{item.unit}]")
        print(f"    источник: {item.source}")
        if item.rationale:
            print(f"    почему  : {item.rationale}")
    print(
        f"\nВсего {len(items)}; «!» — допущение команды, «*» — значение заказчика "
        "(docs/customer_qa.md)."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Собрать разбор аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog="python -m core.cli",
        description="Планировщик беспилотных авиационных работ — ядро без веба",
    )
    subparsers = parser.add_subparsers(dest="command")

    plan = subparsers.add_parser("plan", help="прогнать конвейер (команда по умолчанию)")
    plan.add_argument("scenario", nargs="?", help="путь к JSON-сценарию")
    plan.add_argument(
        "-o",
        "--output",
        default="out",
        help="каталог (весь комплект файлов) или файл (формат по расширению)",
    )
    plan.add_argument(
        "--board",
        help="в файл — задание одного борта, например geoscan-gemini_1 (docs/export_format.md)",
    )
    plan.add_argument("--batch", help="каталог со сценариями: прогнать все подряд")
    plan.add_argument(
        "--mode",
        choices=[mode.value for mode in OptimizationMode],
        help="режим оптимизации, перекрывает сценарий",
    )
    plan.add_argument("--alpha", type=float, help="вес срока в свёртке 0..1 (фронт Парето)")
    plan.add_argument("--seed", type=int, help="зерно ГПСЧ, перекрывает сценарий")
    plan.add_argument(
        "--allocator",
        choices=ALLOCATORS,
        default=ALLOCATORS[0],
        help=(
            "алгоритм распределения: эвристика с локальным поиском (по умолчанию), "
            "эвристика без поиска или baseline"
        ),
    )
    plan.add_argument("--vehicles", help="путь к каталогу техники вместо data/vehicles.yaml")
    plan.add_argument("--dump-stages", help="каталог для выгрузки промежуточных стадий")
    plan.set_defaults(handler=command_plan)

    layers = subparsers.add_parser(
        "import-layers", help="собрать сценарий из слоёв KML и файла параметров"
    )
    layers.add_argument("--area", required=True, help="KML области съёмки")
    layers.add_argument("--zones", help="KML зон ограничений")
    layers.add_argument("--obstacles", help="KML высотных препятствий")
    layers.add_argument(
        "--params", required=True, help="JSON с параметрами задания: флот, площадки, GSD, сроки"
    )
    layers.add_argument(
        "--terrain-min", type=float, default=0.0, help="минимальная отметка рельефа области, м"
    )
    layers.add_argument(
        "--terrain-max", type=float, default=0.0, help="максимальная отметка рельефа области, м"
    )
    layers.add_argument("-o", "--output", required=True, help="куда записать сценарий")
    layers.set_defaults(handler=command_import_layers)

    export_layers = subparsers.add_parser(
        "export-layers", help="выгрузить сценарий слоями KML и параметрами JSON"
    )
    export_layers.add_argument("scenario", help="путь к JSON-сценарию")
    export_layers.add_argument("-o", "--output", required=True, help="каталог комплекта")
    export_layers.set_defaults(handler=command_export_layers)

    compare = subparsers.add_parser(
        "compare", help="сравнить распределение baseline и эвристики на сценариях (E12)"
    )
    compare.add_argument("scenarios", nargs="+", help="файлы сценариев или каталоги с ними")
    compare.add_argument(
        "--mode",
        choices=[mode.value for mode in OptimizationMode],
        help="только этот режим; по умолчанию оба",
    )
    compare.add_argument("--vehicles", help="путь к каталогу техники вместо data/vehicles.yaml")
    compare.add_argument("-o", "--output", help="куда записать таблицу Markdown")
    compare.set_defaults(handler=command_compare)

    hints = subparsers.add_parser(
        "hints", help="подсказки по пулу: срок при N бортах, сколько бортов к сроку"
    )
    hints.add_argument("scenario", help="путь к JSON-сценарию; его флот — пул техники")
    question = hints.add_mutually_exclusive_group()
    question.add_argument("--boards", type=int, help="сколько времени займёт работа при N бортах")
    question.add_argument(
        "--sweep", action="store_true", help="срок при каждом N — от одного борта до всего пула"
    )
    hints.add_argument(
        "--deadline-h",
        type=float,
        help="срок от начала работ, часы: один — сколько бортов нужно; "
        "с --boards и --sweep — успевают ли",
    )
    hints.add_argument("--seed", type=int, help="зерно ГПСЧ, перекрывает сценарий")
    hints.add_argument("--vehicles", help="путь к каталогу техники вместо data/vehicles.yaml")
    hints.add_argument("-o", "--output", help="куда записать отчёт: .json — отчёт, иначе Markdown")
    hints.add_argument("--plan-out", help="каталог: комплект файлов плана из ответа")
    hints.set_defaults(handler=command_hints)

    relayout = subparsers.add_parser(
        "relayout-area", help="разложить островки настоящей области по новому месту"
    )
    relayout.add_argument("--area", required=True, help="KML настоящей области съёмки")
    relayout.add_argument("--center", required=True, help="центр нового места: долгота,широта")
    relayout.add_argument(
        "--size-km",
        type=float,
        nargs=2,
        required=True,
        metavar=("ШИРИНА", "ВЫСОТА"),
        help="прямоугольник нового места, км (восток — запад, север — юг)",
    )
    relayout.add_argument("--seed", type=int, required=True, help="зерно раскладки")
    relayout.add_argument(
        "--link-m",
        type=float,
        default=DEFAULT_LINK_M,
        help="порог соседства: островки ближе него переносятся одной группой, м",
    )
    relayout.add_argument("--title", help="название слоя в KML")
    relayout.add_argument("-o", "--output", required=True, help="куда записать KML области")
    relayout.set_defaults(handler=command_relayout_area)

    check = subparsers.add_parser(
        "check-sources", help="сверить сценарии с исходными файлами из манифеста"
    )
    check.add_argument("--manifest", help="манифест вместо scenarios/sources/manifest.json")
    check.set_defaults(handler=command_check_sources)

    schema = subparsers.add_parser("export-schema", help="выгрузить JSON Schema контракта")
    schema.add_argument(
        "-o", "--output", default="web/src/types/schema.json", help="путь JSON Schema"
    )
    schema.set_defaults(handler=command_export_schema)

    registry = subparsers.add_parser("assumptions", help="напечатать реестр допущений")
    registry.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    registry.add_argument("--team-only", action="store_true", help="только допущения команды")
    registry.set_defaults(handler=command_assumptions)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI.

    Args:
        argv: аргументы без имени программы. None — взять из `sys.argv`.

    Returns:
        Код возврата процесса: 0 — успех, 1 — сценарий не прошёл, 2 — ошибка
        вызова.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    # `python -m core.cli scenarios/simple.json` — путь вместо подкоманды.
    # Подставляем `plan`, чтобы документированная короткая форма работала.
    if not args:
        args = ["plan", "--help"]
    elif args[0] not in SUBCOMMANDS and args[0] not in ("-h", "--help"):
        args.insert(0, "plan")

    parsed = build_parser().parse_args(args)
    if parsed.command == "plan" and not parsed.scenario and not parsed.batch:
        print("Укажите сценарий или --batch с каталогом сценариев", file=sys.stderr)
        return 2

    try:
        return int(parsed.handler(parsed))
    except CoreError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
