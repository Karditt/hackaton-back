"""Фоновые задачи расчёта и их состояние.

Celery, Redis и RabbitMQ запрещены брифом (§3): долгий расчёт выполняется
фоновой задачей внутри процесса API, клиент опрашивает статус поллингом.
WebSocket — «под вопросом», только если останется время (docs/dev/AGENT.md §9).

Состояние идущих задач живёт в памяти процесса. Готовый план пишется на диск
(`storage/jobs/<id>/`: задача, сценарий, результат) и переживает перезапуск —
это история прогонов (E9): после запуска список задач восстанавливается сразу,
а сам план читается с диска при первом обращении. Многопользовательский режим
в объём не входит (docs/dev/PROJECT_BRIEF.md §3).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import multiprocessing
import shutil
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core import assumptions
from core.errors import CoreError
from core.hints import run_hint
from core.pareto import ParetoPoint, pareto_front
from core.pipeline import ALLOCATORS, PlanningRun, restore_run, run_pipeline
from core.schemas import (
    HintKind,
    HintReport,
    HintRun,
    OptimizationMode,
    PipelineResult,
    Scenario,
)
from core.sites import SiteTrial, suggest_sites

#: Сколько готовых планов хранить на диске: старые удаляются. План на данных
#: заказчика — десятки мегабайт; тридцати хватает на рабочий день сравнений.
JOB_HISTORY_LIMIT = 30

logger = logging.getLogger(__name__)


class JobState(str, Enum):
    """Состояние фоновой задачи."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    """Одна задача расчёта.

    План (`run`) либо посчитан в этом процессе, либо лежит на диске от прошлого
    запуска — тогда он читается при первом обращении (`loader`).
    """

    id: str
    scenario_id: str
    state: JobState = JobState.PENDING
    progress: float = 0.0
    error: str | None = None
    cancelled: bool = False
    executor: Any = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    mission_id: str | None = None
    mission_status: str | None = None
    algorithm: str | None = None
    """Алгоритм распределения плана: `baseline:…` — точка сравнения."""
    listed: bool = True
    """Показывать ли в истории расчётов: планы точек фронта Парето — нет, их
    открывают из самого фронта."""
    loader: Callable[[], PlanningRun] | None = field(default=None, repr=False)
    _run: PlanningRun | None = field(default=None, repr=False)
    # Маршруты API без `async` идут в пуле потоков, и веб просит результат и
    # выгрузку разом: план с диска читает один поток, остальные ждут его.
    _loading: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def run(self) -> PlanningRun | None:
        """План расчёта; с диска — при первом обращении."""
        if self._run is None and self.loader is not None:
            with self._loading:
                if self._run is None and self.loader is not None:
                    self._run = self.loader()
                    self.loader = None
        return self._run

    @run.setter
    def run(self, value: PlanningRun | None) -> None:
        self._run = value
        self.loader = None
        self.mission_id = value.mission.id if value else None
        self.mission_status = value.mission.status.value if value else None
        self.algorithm = value.mission.algorithm if value else None

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
            "mission_id": self.mission_id,
            "mission_status": self.mission_status,
            "algorithm": self.algorithm,
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


@dataclass
class ParetoJob:
    """Задача фронта Парето «срок — ресурс»: несколько планов с краш-тестом (E12, №52).

    Точки появляются по мере расчёта; план каждой заведён задачей расчёта
    (`plan_job_id`) и открывается как обычный расчёт — без записи в историю.
    """

    id: str
    scenario_id: str
    expected_points: int
    """Сколько планов ожидается: два края и промежуточные сроки."""
    state: JobState = JobState.PENDING
    points: list[dict[str, Any]] = field(default_factory=list)
    """Готовые точки: в порядке расчёта, а по окончании — по возрастанию срока."""
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def progress(self) -> float:
        """Доля готовности 0..1."""
        if self.state is JobState.DONE:
            return 1.0
        return min(0.95, len(self.points) / max(1, self.expected_points))

    def public(self) -> dict[str, Any]:
        """Представление задачи для клиента: состояние и готовые точки."""
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "state": self.state.value,
            "progress": self.progress,
            "expected_points": self.expected_points,
            "points": list(self.points),
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


@dataclass
class SitesJob:
    """Задача подбора места новой площадки: базовый расчёт и кандидаты (№28).

    Каждая проба — план с краш-тестом, заведён задачей расчёта `plan_job_id`
    вне истории: на его карте видна предложенная площадка.
    """

    id: str
    scenario_id: str
    expected_trials: int
    """Сколько расчётов ожидается: базовый и по кандидату на долю площади."""
    state: JobState = JobState.PENDING
    trials: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def progress(self) -> float:
        """Доля готовности 0..1; кандидатов может оказаться меньше долей — не больше 0,95."""
        if self.state is JobState.DONE:
            return 1.0
        return min(0.95, len(self.trials) / max(1, self.expected_trials))

    def public(self) -> dict[str, Any]:
        """Представление задачи для клиента: состояние и готовые пробы."""
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "state": self.state.value,
            "progress": self.progress,
            "expected_trials": self.expected_trials,
            "trials": list(self.trials),
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def _trial_public(trial: SiteTrial, plan_job_id: str) -> dict[str, Any]:
    """Проба площадки для клиента: место, итог плана и задача, где он лежит."""
    return {
        "label": trial.label,
        "position": trial.position.model_dump(mode="json") if trial.position else None,
        "share": trial.share,
        "feasible": trial.feasible,
        "makespan_s": trial.makespan_s,
        "resource_s": trial.resource_s,
        "elapsed_s": round(trial.elapsed_s, 1),
        "plan_job_id": plan_job_id,
    }


def _point_public(point: ParetoPoint, plan_job_id: str | None) -> dict[str, Any]:
    """Точка фронта для клиента: итог плана и задача, где он лежит."""
    return {
        "label": point.label,
        "mode": point.mode.value,
        "deadline_s": point.deadline_s,
        "feasible": point.feasible,
        "makespan_s": point.makespan_s,
        "resource_s": point.resource_s,
        "sortie_count": point.sortie_count,
        "vehicle_count": point.vehicle_count,
        "elapsed_s": round(point.elapsed_s, 1),
        "dominated": point.dominated,
        "plan_job_id": plan_job_id,
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


def _load_run(directory: Path) -> PlanningRun:
    """План задачи из её каталога: сценарий и результат — ядром, без расчёта."""
    scenario = Scenario.model_validate_json((directory / "scenario.json").read_text("utf-8"))
    result = PipelineResult.model_validate_json((directory / "result.json").read_text("utf-8"))
    return restore_run(scenario, result)


class JobRegistry:
    """Реестр задач расчёта: идущие — в памяти, готовые планы — ещё и на диске."""

    def __init__(self, directory_of: Callable[[], Path] | None = None) -> None:
        """Реестр задач.

        Args:
            directory_of: где хранить готовые планы — функция, а не путь:
                каталог хранилища API может смениться до запуска (тесты).
                None — только в памяти.
        """
        self._directory_of = directory_of
        self._jobs: dict[str, Job] = {}
        self._by_mission: dict[str, Job] = {}
        self._hints: dict[str, HintJob] = {}
        self._fronts: dict[str, ParetoJob] = {}
        self._sites: dict[str, SitesJob] = {}
        # Ссылки на задачи держатся явно: asyncio хранит их только слабо,
        # и без этого сборщик мусора может снять расчёт на середине.
        self._tasks: set[asyncio.Task[None]] = set()

    def _directory(self) -> Path | None:
        return self._directory_of() if self._directory_of is not None else None

    def restore(self) -> int:
        """Поднять с диска готовые планы прошлых запусков — без чтения самих планов.

        Каталог, который не читается, пропускается с записью в журнал.

        Returns:
            Число восстановленных задач.
        """
        directory = self._directory()
        if directory is None or not directory.is_dir():
            return 0
        restored = 0
        for path in sorted(directory.glob("*/job.json")):
            try:
                meta = json.loads(path.read_text("utf-8"))
                job = Job(
                    id=meta["id"],
                    scenario_id=meta["scenario_id"],
                    state=JobState.DONE,
                    progress=1.0,
                    created_at=datetime.fromisoformat(meta["created_at"]),
                    finished_at=datetime.fromisoformat(meta["finished_at"]),
                    mission_id=meta["mission_id"],
                    mission_status=meta["mission_status"],
                    algorithm=meta.get("algorithm"),
                    loader=lambda folder=path.parent: _load_run(folder),
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                logger.warning("Сохранённая задача %s не прочитана: %s", path.parent.name, error)
                continue
            if job.id in self._jobs:
                continue
            self._jobs[job.id] = job
            if job.mission_id:
                self._by_mission[job.mission_id] = job
            restored += 1
        return restored

    def _persist(self, job: Job) -> None:
        """Записать готовый план на диск и оставить только последние `JOB_HISTORY_LIMIT`.

        Сбой записи план не отменяет: задача остаётся в памяти, причина — в журнале.
        """
        directory = self._directory()
        run = job.run
        if directory is None or run is None:
            return
        folder = directory / job.id
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "scenario.json").write_text(run.scenario.model_dump_json(), "utf-8")
            (folder / "result.json").write_text(run.result.model_dump_json(), "utf-8")
            # Описание задачи — последним: по нему задача и восстанавливается.
            (folder / "job.json").write_text(json.dumps(job.public(), ensure_ascii=False), "utf-8")
        except (OSError, ValidationError) as error:
            logger.warning("План задачи %s не сохранён: %s", job.id, error)
            return
        stored = sorted(directory.glob("*/job.json"), key=lambda path: path.stat().st_mtime)
        for path in stored[:-JOB_HISTORY_LIMIT]:
            stale = path.parent
            shutil.rmtree(stale, ignore_errors=True)
            forgotten = self._jobs.pop(stale.name, None)
            if forgotten is not None and forgotten.mission_id:
                self._by_mission.pop(forgotten.mission_id, None)

    def get(self, job_id: str) -> Job | None:
        """Задача по идентификатору."""
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Остановить расчёт: убить его процесс. False — задачи нет или она уже закончена."""
        job = self._jobs.get(job_id)
        if job is None or job.state not in (JobState.PENDING, JobState.RUNNING):
            return False
        job.cancelled = True
        executor = job.executor
        for process in list(getattr(executor, "_processes", {}).values()):
            process.kill()
        return True

    def by_mission(self, mission_id: str) -> Job | None:
        """Задача, породившая план."""
        return self._by_mission.get(mission_id)

    def all(self) -> list[Job]:
        """Все задачи истории, новые первыми; планы точек фронта Парето — нет."""
        listed = [job for job in self._jobs.values() if job.listed]
        return sorted(listed, key=lambda job: job.created_at, reverse=True)

    def submit(
        self,
        scenario: Scenario,
        mode: OptimizationMode | None = None,
        alpha: float | None = None,
        seed: int | None = None,
        allocator: str = ALLOCATORS[0],
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
            allocator: алгоритм распределения из `ALLOCATORS`.

        Returns:
            Созданная задача в состоянии PENDING.
        """
        job = Job(id=f"job-{uuid.uuid4().hex[:8]}", scenario_id=scenario.id)
        self._jobs[job.id] = job
        task = asyncio.create_task(self._execute(job, scenario, mode, alpha, seed, allocator))
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
        allocator: str,
    ) -> None:
        """Выполнить расчёт и записать результат в задачу.

        Невыполнимое задание — не сбой: задача завершается состоянием DONE, а
        статус плана `infeasible` и причины лежат в результате. FAILED — только
        ошибка входа или внутренняя ошибка.
        """
        job.state = JobState.RUNNING
        # Отдельный процесс: поток остановить нельзя, а процесс — можно (отмена).
        job.executor = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"))
        try:
            run = await asyncio.get_running_loop().run_in_executor(
                job.executor, run_pipeline, scenario, None, mode, alpha, seed, allocator
            )
        except Exception as error:  # noqa: BLE001
            if job.cancelled:
                job.state = JobState.FAILED
                job.error = "Расчёт отменён"
            elif isinstance(error, CoreError):
                job.state = JobState.FAILED
                job.error = str(error)
            else:
                job.state = JobState.FAILED
                job.error = "Внутренняя ошибка расчёта:\n" + traceback.format_exc(limit=5)
        else:
            job.run = run
            job.state = JobState.DONE
            job.progress = 1.0
            job.finished_at = datetime.now(timezone.utc)
            self._by_mission[run.mission.id] = job
            await asyncio.to_thread(self._persist, job)
        finally:
            job.finished_at = job.finished_at or datetime.now(timezone.utc)
            job.executor.shutdown(wait=False, cancel_futures=True)
            job.executor = None

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
                )
                plan_job.run = result.plan
                self._jobs[plan_job.id] = plan_job
                self._by_mission[result.plan.mission.id] = plan_job
                job.plan_job_id = plan_job.id
                await asyncio.to_thread(self._persist, plan_job)
            job.state = JobState.DONE
        finally:
            job.finished_at = datetime.now(timezone.utc)

    def front(self, front_id: str) -> ParetoJob | None:
        """Задача фронта Парето по идентификатору."""
        return self._fronts.get(front_id)

    def submit_pareto(self, scenario: Scenario, steps: int = assumptions.PARETO_STEPS) -> ParetoJob:
        """Поставить фронт «срок — ресурс» в расчёт — как `submit`, в пул потоков.

        Args:
            scenario: сценарий; его флот — пул, его срок не учитывается.
            steps: сколько промежуточных сроков между краями.

        Returns:
            Созданная задача в состоянии PENDING.
        """
        job = ParetoJob(
            id=f"front-{uuid.uuid4().hex[:8]}",
            scenario_id=scenario.id,
            expected_points=steps + 2,
        )
        self._fronts[job.id] = job
        task = asyncio.create_task(self._execute_pareto(job, scenario, steps))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _execute_pareto(self, job: ParetoJob, scenario: Scenario, steps: int) -> None:
        """Построить фронт; план каждой точки — задача расчёта вне истории.

        Точки идут в `job.points` по мере расчёта (из потока расчёта — список
        только пополняется), а в конце заменяются итоговыми: по возрастанию
        срока и с пометкой доминируемых.
        """
        plan_jobs: dict[tuple[str, float | None], str] = {}

        def on_plan(point: ParetoPoint, run: PlanningRun) -> None:
            plan_job_id = self._plan_job(run)
            plan_jobs[(point.mode.value, point.deadline_s)] = plan_job_id
            job.points.append(_point_public(point, plan_job_id))

        job.state = JobState.RUNNING
        try:
            points = await asyncio.to_thread(pareto_front, scenario, None, steps, None, on_plan)
        except CoreError as error:
            job.state = JobState.FAILED
            job.error = str(error)
        except Exception:  # noqa: BLE001 — иначе задача падает молча
            job.state = JobState.FAILED
            job.error = "Внутренняя ошибка расчёта фронта:\n" + traceback.format_exc(limit=5)
        else:
            job.points = [
                _point_public(point, plan_jobs.get((point.mode.value, point.deadline_s)))
                for point in points
            ]
            job.state = JobState.DONE
        finally:
            job.finished_at = datetime.now(timezone.utc)

    def site_search(self, search_id: str) -> SitesJob | None:
        """Задача подбора площадки по идентификатору."""
        return self._sites.get(search_id)

    def submit_sites(self, scenario: Scenario) -> SitesJob:
        """Поставить подбор места новой площадки в расчёт — как `submit`, в пул потоков.

        Args:
            scenario: сценарий; его пул и площадки.

        Returns:
            Созданная задача в состоянии PENDING.
        """
        job = SitesJob(
            id=f"sites-{uuid.uuid4().hex[:8]}",
            scenario_id=scenario.id,
            expected_trials=1 + len(assumptions.SITE_SUGGEST_SHARES),
        )
        self._sites[job.id] = job
        task = asyncio.create_task(self._execute_sites(job, scenario))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def _plan_job(self, run: PlanningRun) -> str:
        """Завести готовый план задачей расчёта вне истории; вернуть её идентификатор."""
        plan_job = Job(
            id=f"job-{uuid.uuid4().hex[:8]}",
            scenario_id=run.scenario.id,
            state=JobState.DONE,
            progress=1.0,
            finished_at=datetime.now(timezone.utc),
            listed=False,
        )
        plan_job.run = run
        self._jobs[plan_job.id] = plan_job
        self._by_mission[run.mission.id] = plan_job
        return plan_job.id

    async def _execute_sites(self, job: SitesJob, scenario: Scenario) -> None:
        """Подобрать площадку; план каждой пробы — задача расчёта вне истории."""

        def on_plan(trial: SiteTrial, run: PlanningRun) -> None:
            job.trials.append(_trial_public(trial, self._plan_job(run)))

        job.state = JobState.RUNNING
        try:
            await asyncio.to_thread(
                suggest_sites, scenario, None, assumptions.SITE_SUGGEST_SHARES, None, on_plan
            )
        except CoreError as error:
            job.state = JobState.FAILED
            job.error = str(error)
        except Exception:  # noqa: BLE001 — иначе задача падает молча
            job.state = JobState.FAILED
            job.error = "Внутренняя ошибка подбора площадки:\n" + traceback.format_exc(limit=5)
        else:
            job.state = JobState.DONE
        finally:
            job.finished_at = datetime.now(timezone.utc)


__all__ = ["HintJob", "Job", "JobRegistry", "JobState", "ParetoJob", "SitesJob"]
