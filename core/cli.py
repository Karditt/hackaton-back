"""Командная строка ядра: прогон конвейера и служебные команды.

ИНВАРИАНТ 2: ядро запускается без API и без веба. Если `api/` удалить из
репозитория целиком, всё, что здесь описано, обязано продолжать работать.

    python -m core.cli scenarios/simple.json -o out/mission.geojson
    python -m core.cli scenarios/simple.json -o out/gemini1.kml --board geoscan-gemini_1
    python -m core.cli --batch scenarios/ -o out/
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
from core.export.package import write_mission_files
from core.export.writer import export_mission, format_for
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import (
    OptimizationMode,
    PipelineResult,
    PlanWarning,
    Scenario,
    Severity,
    Vehicle,
)

#: Подкоманды, которые не являются путём к сценарию.
SUBCOMMANDS = ("plan", "export-schema", "assumptions")


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
                scenario, catalog=catalog, mode=mode, alpha=args.alpha, seed=args.seed
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


def command_export_schema(args: argparse.Namespace) -> int:
    """Выгрузить JSON Schema контракта для генерации TypeScript-типов.

    AGENT.md §4: после ЛЮБОЙ правки `core/schemas.py` эта команда прогоняется
    заново, иначе типы фронта расходятся с ядром молча.

    Корни выгрузки — то, что фронт получает от API целиком: сценарий
    (`GET /scenarios/{id}`), результат прогона (`GET /jobs/{id}/result`) и
    борт каталога (`GET /vehicles`). `Vehicle` не достижим из первых двух,
    поэтому назван отдельно: иначе типы каталога пришлось бы писать руками,
    что запрещено (AGENT.md §2).
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
        },
        "definitions": {},
    }

    for model in (Scenario, PipelineResult, Vehicle):
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
    plan.add_argument("--vehicles", help="путь к каталогу техники вместо data/vehicles.yaml")
    plan.add_argument("--dump-stages", help="каталог для выгрузки промежуточных стадий")
    plan.set_defaults(handler=command_plan)

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
