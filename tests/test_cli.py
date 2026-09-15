"""CLI ядра: инвариант 2 — ядро работает без веба."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.cli import main
from tests.conftest import SCENARIOS_DIR


def test_core_does_not_import_web_layers() -> None:
    """ИНВАРИАНТ 2: ни одного импорта из `api` или `web` в ядре.

    Проверка текстовая, а не через `sys.modules`: импорт внутри функции или
    под `TYPE_CHECKING` не всплыл бы в загруженных модулях, но нарушением
    быть не перестал бы.
    """
    core_dir = Path(__file__).resolve().parents[1] / "core"
    offenders: list[str] = []
    for path in core_dir.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import api", "from api", "import web", "from web")):
                offenders.append(f"{path.name}:{number}: {stripped}")
    assert not offenders, "ядро не имеет права знать про веб:\n" + "\n".join(offenders)


def test_plan_writes_geojson(tmp_path: Path) -> None:
    """Документированная короткая форма вызова работает без подкоманды."""
    output = tmp_path / "mission.geojson"
    code = main([str(SCENARIOS_DIR / "simple.json"), "-o", str(output)])
    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["type"] == "FeatureCollection"


def test_batch_runs_every_scenario(tmp_path: Path) -> None:
    """`--batch` прогоняет весь каталог и пишет оба формата на каждый сценарий."""
    code = main(["--batch", str(SCENARIOS_DIR), "-o", str(tmp_path)])
    assert code == 0
    for path in SCENARIOS_DIR.glob("*.json"):
        assert (tmp_path / f"{path.stem}.geojson").is_file()
        assert (tmp_path / f"{path.stem}.kml").is_file()


def test_export_schema_produces_valid_json(tmp_path: Path) -> None:
    """Контракт выгружается в JSON Schema — вход генератора TypeScript-типов."""
    output = tmp_path / "schema.json"
    assert main(["export-schema", "-o", str(output)]) == 0
    schema = json.loads(output.read_text(encoding="utf-8"))
    assert "Scenario" in schema["definitions"]
    assert "PipelineResult" in schema["definitions"]


def test_assumptions_json_lists_sources(capsys: pytest.CaptureFixture[str]) -> None:
    """Реестр допущений выгружается машиночитаемо — основа раздела ограничений."""
    assert main(["assumptions", "--json"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert items
    assert all(item["source"] for item in items)


def test_missing_scenario_returns_error_code(tmp_path: Path) -> None:
    """Несуществующий сценарий — понятный код возврата, а не трассировка."""
    assert main([str(tmp_path / "nope.json"), "-o", str(tmp_path / "o.geojson")]) == 1


def test_no_arguments_is_not_a_crash() -> None:
    """Пустой вызов печатает справку и завершается кодом 0."""
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 0


def test_dump_stages_writes_intermediate_results(tmp_path: Path) -> None:
    """Инспектор стадий (E9/E11) кормится этим файлом."""
    code = main(
        [
            str(SCENARIOS_DIR / "simple.json"),
            "-o",
            str(tmp_path / "mission.geojson"),
            "--dump-stages",
            str(tmp_path),
        ]
    )
    assert code == 0
    stages = json.loads((tmp_path / "simple.stages.json").read_text(encoding="utf-8"))
    assert {"survey_plan", "coverage", "allocation", "mission"} <= set(stages)
