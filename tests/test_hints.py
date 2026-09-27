"""Подсказки по пулу техники (заказчик, №51): срок при N бортах, сколько бортов к сроку.

Подсказка — несколько обычных прогонов конвейера, поэтому здесь проверяется
выбор бортов и логика вопросов, а не физика: её покрывают тесты стадий.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.catalog import VehicleCatalog
from core.cli import load_scenario, main
from core.hints import (
    _Session,
    boards_hint,
    boards_text,
    deadline_hint,
    duration_text,
    fleet_of,
    pool_boards,
    sweep_hint,
)
from core.pipeline import run_pipeline
from core.schemas import HintKind, HintReport, HintRun, MissionStatus, OptimizationMode
from tests.conftest import SCENARIOS_DIR


@pytest.fixture(scope="module")
def medium():
    return load_scenario(SCENARIOS_DIR / "medium.json")


@pytest.fixture(scope="module")
def demo():
    return load_scenario(SCENARIOS_DIR / "demo_100km2.json")


def test_pool_is_ordered_by_productivity(demo, catalog: VehicleCatalog) -> None:
    """Самолёты первыми; равные — в порядке сценария; 801 без камеры — не в пуле."""
    pool, warnings = pool_boards(demo, catalog)
    assert [board.vehicle.id for board in pool[:3]] == ["geoscan-201"] * 3
    productivity = [board.productivity_m2ps for board in pool]
    assert productivity == sorted(productivity, reverse=True)
    assert [board.unit.site_id for board in pool[3:]] == [
        "site-north",
        "site-north",
        "site-east",
        "site-east",
        "site-south",
        "site-west",
    ]
    assert len(pool) == 9
    assert [warning.code for warning in warnings] == ["hints.board_excluded"]
    assert "Геоскан 801" in warnings[0].message


def test_fleet_keeps_row_attributes(demo, catalog: VehicleCatalog) -> None:
    """Из пула берутся борта, а приписка к площадке, нагрузка и пределы — строки."""
    pool, _ = pool_boards(demo, catalog)
    fleet = fleet_of([pool[3], pool[0], pool[5]])
    assert [(unit.vehicle_id, unit.count, unit.site_id) for unit in fleet] == [
        ("geoscan-201", 1, None),
        ("geoscan-gemini", 1, "site-north"),
        ("geoscan-gemini", 1, "site-east"),
    ]
    source = {unit.site_id: unit for unit in demo.fleet}
    assert fleet[1].payload_id == source["site-north"].payload_id


@pytest.mark.parametrize(
    ("count", "nom", "gen", "loc"),
    [
        (1, "1 борт", "1 борта", "1 борте"),
        (2, "2 борта", "2 бортов", "2 бортах"),
        (5, "5 бортов", "5 бортов", "5 бортах"),
        (11, "11 бортов", "11 бортов", "11 бортах"),
        (12, "12 бортов", "12 бортов", "12 бортах"),
        (21, "21 борт", "21 борта", "21 борте"),
    ],
)
def test_boards_text_cases(count: int, nom: str, gen: str, loc: str) -> None:
    """Ответ читает человек: число бортов — в нужном падеже."""
    assert boards_text(count) == nom
    assert boards_text(count, "gen") == gen
    assert boards_text(count, "loc") == loc


def test_duration_text() -> None:
    assert duration_text(45 * 60) == "45 мин"
    assert duration_text(3 * 3600) == "3 ч"
    assert duration_text(12 * 3600 + 5 * 60 + 10) == "12 ч 5 мин"


def test_whole_pool_is_the_ordinary_plan(medium, catalog: VehicleCatalog) -> None:
    """Подсказка на весь пул — тот же план, что обычный расчёт сценария."""
    result = boards_hint(medium, 4, catalog)
    plain = run_pipeline(medium, catalog=catalog, mode=OptimizationMode.MIN_MAKESPAN)
    (run,) = result.report.runs
    assert run.status is MissionStatus.FEASIBLE
    assert run.makespan_s == pytest.approx(plain.mission.metrics.makespan_s)
    assert result.plan is not None
    assert result.plan.mission.metrics.makespan_s == pytest.approx(run.makespan_s)
    assert result.report.answer_boards == 4
    assert result.report.answer.startswith("4 борта (1 × Геоскан 201, 3 × Геоскан Gemini)")


def test_composition_is_repaired_when_coverage_fails(demo, catalog: VehicleCatalog) -> None:
    """Два самолёта не снимают подобласть у зоны — второй меняется на коптер.

    Самые производительные два борта демо — два 201, и у зоны остаётся
    подобласть, где самолёту не развернуться. Подсказка не должна сказать
    «2 борта — невыполнимо», если 201 и Gemini справляются.
    """
    result = boards_hint(demo, 2, catalog)
    (run,) = result.report.runs
    assert run.status is MissionStatus.FEASIBLE
    assert sorted(unit.vehicle_id for unit in run.fleet) == ["geoscan-201", "geoscan-gemini"]
    assert "Геоскан Gemini вместо Геоскан 201" in run.note
    assert result.plan is not None and result.plan.mission.is_feasible()


def test_deadline_finds_the_smallest_fleet(medium, catalog: VehicleCatalog) -> None:
    """Сколько бортов к сроку: наименьшее N, план которого успевает; N − 1 — нет."""
    whole = boards_hint(medium, 4, catalog).report.runs[0].makespan_s
    deadline_s = whole * 1.3
    result = deadline_hint(medium, deadline_s, catalog)
    report = result.report
    answer = report.answer_boards
    assert answer is not None
    runs = {run.boards: run for run in report.runs}
    assert runs[answer].meets_deadline
    assert answer == 1 or not runs[answer - 1].meets_deadline
    assert 4 in runs, "весь пул проверяется первым"
    assert result.plan is not None
    assert result.plan.mission.metrics.makespan_s <= deadline_s
    assert report.answer.startswith(f"Чтобы успеть за {duration_text(deadline_s)}, нужно")


def test_deadline_beyond_the_pool_has_no_answer(medium, catalog: VehicleCatalog) -> None:
    """Не успевает весь пул — ответа нет, а причина названа; один прогон."""
    result = deadline_hint(medium, 600.0, catalog)
    assert result.report.answer_boards is None
    assert result.plan is None
    assert [run.boards for run in result.report.runs] == [4]
    assert result.report.answer.startswith("Не успеть даже всем пулом")


def test_sweep_covers_every_n(catalog: VehicleCatalog) -> None:
    """Перебор — каждое N от 1 до пула; ответ — самый короткий срок."""
    result = sweep_hint(load_scenario(SCENARIOS_DIR / "simple.json"), catalog)
    report = result.report
    assert report.kind is HintKind.SWEEP
    assert [run.boards for run in report.runs] == [1, 2]
    assert report.runs[0].status is MissionStatus.INFEASIBLE
    assert report.runs[0].reasons, "у невыполнимого прогона — причина"
    assert report.answer_boards == 2
    assert report.answer.startswith("Выполнимо начиная с 2 бортов")


def test_more_boards_than_the_pool(catalog: VehicleCatalog) -> None:
    """Спросили больше, чем есть, — считается весь пул, с предупреждением."""
    report = boards_hint(load_scenario(SCENARIOS_DIR / "simple.json"), 5, catalog).report
    assert [run.boards for run in report.runs] == [2]
    assert "hints.pool_short" in {warning.code for warning in report.warnings}


def test_same_question_same_answer(medium, catalog: VehicleCatalog) -> None:
    """Детерминированность (AGENT.md §8): тот же вопрос — те же прогоны."""
    first = sweep_hint(medium, catalog).report
    second = sweep_hint(medium, catalog).report
    assert [(run.boards, run.makespan_s, run.note) for run in first.runs] == [
        (run.boards, run.makespan_s, run.note) for run in second.runs
    ]


def test_slower_with_more_boards_is_reported(medium, catalog: VehicleCatalog) -> None:
    """Лишний борт ухудшил срок — отчёт это говорит, а не прячет."""
    session = _Session(medium, catalog, None, None, None)
    fleet = medium.fleet[:1]
    for count, makespan_s in ((1, 7200.0), (2, 3600.0), (3, 5400.0)):
        session.runs[count] = HintRun(
            boards=count,
            fleet=fleet,
            status=MissionStatus.FEASIBLE,
            makespan_s=makespan_s,
            elapsed_s=0.0,
        )
    report = session.report(HintKind.SWEEP, "—")
    slower = [warning for warning in report.warnings if warning.code == "hints.more_is_slower"]
    assert len(slower) == 1
    assert "при 3 бортах срок 1 ч 30 мин, при 2 бортах — 1 ч" in slower[0].message


def test_invalid_questions_are_rejected(medium, catalog: VehicleCatalog) -> None:
    with pytest.raises(ValueError):
        boards_hint(medium, 0, catalog)
    with pytest.raises(ValueError):
        deadline_hint(medium, 0.0, catalog)


def test_cli_hints_writes_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI: вопрос, таблица по мере прогонов, ответ и отчёт JSON по контракту."""
    output = tmp_path / "hints.json"
    code = main(["hints", str(SCENARIOS_DIR / "simple.json"), "--boards", "2", "-o", str(output)])
    assert code == 0
    report = HintReport.model_validate(json.loads(output.read_text(encoding="utf-8")))
    assert report.answer_boards == 2
    assert "ОТВЕТ: 2 борта" in capsys.readouterr().out


def test_cli_hints_needs_a_question(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["hints", str(SCENARIOS_DIR / "simple.json")]) == 2
