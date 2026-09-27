"""Фоновые задачи расчёта и их состояние.

Celery, Redis и RabbitMQ запрещены брифом (§3): долгий расчёт выполняется
фоновой задачей внутри процесса API, клиент опрашивает статус поллингом.
WebSocket — «под вопросом», только если останется время (AGENT.md §9).

Состояние задач живёт в памяти процесса. Многопользовательский режим и
устойчивость к перезапуску в объём не входят (PROJECT_BRIEF.md §3).
"""

from __future__ import annotations

import asyncio
import math
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.errors import CoreError
from core.hints import run_hint
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import HintKind, HintReport, HintRun, OptimizationMode, Scenario


class JobState(str, Enum):
    """Состояние фоновой задачи."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    """Одна задача расчёта."""

    id: str
    scenario_id: str
    state: JobState = JobState.PENDING
    progress: float = 0.0
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    run: PlanningRun | None = None

    def public(self) -> dict[str, Any]:
        """Представление задачи для клиента без несериализуемых полей."""
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "state": self.state.value,
            "progress": self.progress,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "mission_id": self.run.mission.id if self.run else None,
            "mission_status": self.run.mission.status.value if self.run else None,
        }


@dataclass
class HintJob:
    """Задача подсказки по пулу: несколько прогонов конвейера (заказчик, №51)."""

    id: str
    scenario_id: str
    kind: HintKind
    boards: int | None
    deadline_s: float | None
    expected_runs: int
    """Сколько прогонов ожидается — только для прогресса."""
    state: JobState = JobState.PENDING
    runs_done: int = 0
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    report: HintReport | None = None
    plan_job_id: str | None = None
    """Задача с планом из ответа: его стадии, расписание и выгрузка — по `/jobs/{id}/…`."""

    @property
    def progress(self) -> float:
        """Доля готовности 0..1; до конца — не больше 0,95: число прогонов оценочное."""
        if self.state is JobState.DONE:
            return 1.0
        return min(0.95, self.runs_done / max(1, self.expected_runs))

    def public(self) -> dict[str, Any]:
        """Представление задачи для клиента: вопрос, состояние, отчёт."""
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "kind": self.kind.value,
            "boards": self.boards,
            "deadline_s": self.deadline_s,
            "state": self.state.value,
            "progress": self.progress,
            "runs_done": self.runs_done,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "report": self.report.model_dump(mode="json") if self.report else None,
            "plan_job_id": self.plan_job_id,
        }


def _expected_runs(scenario: Scenario, kind: HintKind) -> int:
    """Оценка числа прогонов вопроса: для полосы прогресса, не для расчёта."""
    size = sum(unit.count for unit in scenario.fleet)
    if kind is HintKind.BOARDS:
        return 1
    if kind is HintKind.SWEEP:
        return size
    # Весь пул, двоичный поиск и два соседа.
    return 1 + math.ceil(math.log2(max(2, size))) + 2


class JobRegistry:
    """Реестр задач расчёта в памяти процесса."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._by_mission: dict[str, Job] = {}
        self._hints: dict[str, HintJob] = {}
        # Ссылки на задачи держатся явно: asyncio хранит их только слабо,
        # и без этого сборщик мусора может снять расчёт на середине.
        self._tasks: set[asyncio.Task[None]] = set()

    def get(self, job_id: str) -> Job | None:
        """Задача по идентификатору."""
        return self._jobs.get(job_id)

    def by_mission(self, mission_id: str) -> Job | None:
        """Задача, породившая план."""
        return self._by_mission.get(mission_id)

    def all(self) -> list[Job]:
        """Все задачи, новые первыми."""
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def submit(
        self,
        scenario: Scenario,
        mode: OptimizationMode | None = None,
        alpha: float | None = None,
        seed: int | None = None,
    ) -> Job:
        """Поставить сценарий в расчёт.

        Конвейер — синхронный и считает в CPU, поэтому он уходит в пул
        потоков: иначе он заблокировал бы цикл событий и поллинг статуса
        перестал бы отвечать ровно тогда, когда он нужен.

        Вызывается только из асинхронного контекста: цикл событий должен быть
        запущен, иначе задачу некуда поставить.

        Args:
            scenario: сценарий.
            mode: режим оптимизации, перекрывает сценарий.
            alpha: вес срока в свёртке, перекрывает сценарий.
            seed: зерно ГПСЧ, перекрывает сценарий.

        Returns:
            Созданная задача в состоянии PENDING.
        """
        job = Job(id=f"job-{uuid.uuid4().hex[:8]}", scenario_id=scenario.id)
        self._jobs[job.id] = job
        task = asyncio.create_task(self._execute(job, scenario, mode, alpha, seed))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _execute(
        self,
        job: Job,
        scenario: Scenario,
        mode: OptimizationMode | None,
        alpha: float | None,
        seed: int | None,
    ) -> None:
        """Выполнить расчёт и записать результат в задачу.

        Невыполнимое задание — не сбой: задача завершается состоянием DONE, а
        статус плана `infeasible` и причины лежат в результате. FAILED — только
        ошибка входа или внутренняя ошибка.
        """
        job.state = JobState.RUNNING
        try:
            run = await asyncio.to_thread(run_pipeline, scenario, None, mode, alpha, seed)
        except CoreError as error:
            job.state = JobState.FAILED
            job.error = str(error)
        except Exception:  # noqa: BLE001 — иначе задача падает молча
            job.state = JobState.FAILED
            job.error = "Внутренняя ошибка расчёта:\n" + traceback.format_exc(limit=5)
        else:
            job.run = run
            job.state = JobState.DONE
            job.progress = 1.0
            self._by_mission[run.mission.id] = job
        finally:
            job.finished_at = datetime.now(timezone.utc)

    def hint(self, hint_id: str) -> HintJob | None:
        """Задача подсказки по идентификатору."""
        return self._hints.get(hint_id)

    def submit_hint(
        self,
        scenario: Scenario,
        kind: HintKind,
        boards: int | None = None,
        deadline_s: float | None = None,
        seed: int | None = None,
    ) -> HintJob:
        """Поставить вопрос подсказки в расчёт — как `submit`, в пул потоков.

        Args:
            scenario: сценарий; его флот — пул.
            kind: вопрос.
            boards: число бортов — для `boards`.
            deadline_s: срок от начала работ, секунды.
            seed: зерно ГПСЧ, перекрывает сценарий.

        Returns:
            Созданная задача в состоянии PENDING.
        """
        job = HintJob(
            id=f"hint-{uuid.uuid4().hex[:8]}",
            scenario_id=scenario.id,
            kind=kind,
            boards=boards,
            deadline_s=deadline_s,
            expected_runs=_expected_runs(scenario, kind),
        )
        self._hints[job.id] = job
        task = asyncio.create_task(self._execute_hint(job, scenario, seed))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _execute_hint(self, job: HintJob, scenario: Scenario, seed: int | None) -> None:
        """Ответить на вопрос и завести план из ответа обычной задачей расчёта.

        Ответа нет («не успеть даже всем пулом») — не сбой: задача DONE, ответ
        и прогоны — в отчёте.
        """

        def on_run(_: HintRun) -> None:
            job.runs_done += 1

        job.state = JobState.RUNNING
        try:
            result = await asyncio.to_thread(
                run_hint,
                scenario,
                job.kind,
                job.boards,
                job.deadline_s,
                None,
                seed,
                on_run,
            )
        except (CoreError, ValueError) as error:
            job.state = JobState.FAILED
            job.error = str(error)
        except Exception:  # noqa: BLE001 — иначе задача падает молча
            job.state = JobState.FAILED
            job.error = "Внутренняя ошибка подсказки:\n" + traceback.format_exc(limit=5)
        else:
            job.report = result.report
            if result.plan is not None:
                plan_job = Job(
                    id=f"job-{uuid.uuid4().hex[:8]}",
                    scenario_id=result.plan.scenario.id,
                    state=JobState.DONE,
                    progress=1.0,
                    finished_at=datetime.now(timezone.utc),
                    run=result.plan,
                )
                self._jobs[plan_job.id] = plan_job
                self._by_mission[result.plan.mission.id] = plan_job
                job.plan_job_id = plan_job.id
            job.state = JobState.DONE
        finally:
            job.finished_at = datetime.now(timezone.utc)


__all__ = ["HintJob", "Job", "JobRegistry", "JobState"]
