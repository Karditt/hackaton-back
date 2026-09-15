"""Фоновые задачи расчёта и их состояние.

Celery, Redis и RabbitMQ запрещены брифом (§3): долгий расчёт выполняется
фоновой задачей внутри процесса API, клиент опрашивает статус поллингом.
WebSocket — «под вопросом», только если останется время (AGENT.md §9).

Состояние задач живёт в памяти процесса. Многопользовательский режим и
устойчивость к перезапуску в объём не входят (PROJECT_BRIEF.md §3).
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.errors import CoreError
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import Scenario


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
        }


class JobRegistry:
    """Реестр задач расчёта в памяти процесса."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._by_mission: dict[str, Job] = {}
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
        self, scenario: Scenario, alpha: float | None = None, seed: int | None = None
    ) -> Job:
        """Поставить сценарий в расчёт.

        Конвейер — синхронный и считает в CPU, поэтому он уходит в пул
        потоков: иначе он заблокировал бы цикл событий и поллинг статуса
        перестал бы отвечать ровно тогда, когда он нужен.

        Вызывается только из асинхронного контекста: цикл событий должен быть
        запущен, иначе задачу некуда поставить.

        Args:
            scenario: сценарий.
            alpha: вес makespan, перекрывает сценарий.
            seed: зерно ГПСЧ, перекрывает сценарий.

        Returns:
            Созданная задача в состоянии PENDING.
        """
        job = Job(id=f"job-{uuid.uuid4().hex[:8]}", scenario_id=scenario.id)
        self._jobs[job.id] = job
        task = asyncio.create_task(self._execute(job, scenario, alpha, seed))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _execute(
        self, job: Job, scenario: Scenario, alpha: float | None, seed: int | None
    ) -> None:
        """Выполнить расчёт и записать результат в задачу."""
        job.state = JobState.RUNNING
        try:
            run = await asyncio.to_thread(run_pipeline, scenario, None, alpha, seed)
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


__all__ = ["Job", "JobRegistry", "JobState"]
