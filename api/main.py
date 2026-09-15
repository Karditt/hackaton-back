"""HTTP-интерфейс сервиса.

КАРКАС E9. Маршруты соответствуют брифу: сценарии, запуск конвейера фоновой
задачей, поллинг статуса, доступ к промежуточным стадиям, отдача файлов
экспорта. Авторизации, ролей и многопользовательского режима нет и не будет —
они вне объёма проекта (PROJECT_BRIEF.md §3).

Ни одного расчёта в этом файле: хендлер валидирует запрос, зовёт `core` и
сериализует ответ.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from api.jobs import Job, JobRegistry, JobState
from api.storage import ScenarioStore
from core import __version__, assumptions
from core.catalog import load_catalog
from core.errors import CoreError, ScenarioError
from core.export.geojson import mission_to_geojson
from core.export.kml import mission_to_kml
from core.schemas import ExportFormat, PipelineResult, Scenario

REPO_ROOT = Path(__file__).resolve().parents[1]

store = ScenarioStore()
jobs = JobRegistry()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Подложить тестовые сценарии, чтобы на чистой машине было что показать."""
    store.seed_from(REPO_ROOT / "scenarios")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="GEOSCAN — планировщик беспилотных авиационных работ",
    version=__version__,
    description=(
        "Тонкий слой поверх ядра `core`. Расчёт идёт фоновой задачей внутри "
        "процесса, статус опрашивается поллингом."
    ),
)

# Фронт живёт на другом порту (Vite), поэтому CORS открыт. Ограничивать
# незачем: сервис однопользовательский и работает локально.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _job_or_404(job_id: str) -> Job:
    """Задача по идентификатору либо 404."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Задача {job_id!r} не найдена")
    return job


def _finished_job(job: Job) -> Job:
    """Задача, у которой есть результат, либо осмысленная ошибка."""
    if job.state is JobState.FAILED:
        raise HTTPException(status_code=422, detail=job.error or "Расчёт не удался")
    if job.run is None:
        raise HTTPException(status_code=409, detail=f"Расчёт ещё идёт: {job.state.value}")
    return job


@app.get("/health", tags=["служебные"])
def health() -> dict[str, Any]:
    """Проверка живости сервиса и доступности каталога техники."""
    try:
        catalog = load_catalog()
        vehicles = len(catalog)
        catalog_ok = True
    except CoreError:
        vehicles = 0
        catalog_ok = False
    return {
        "status": "ok" if catalog_ok else "degraded",
        "version": __version__,
        "vehicles_in_catalog": vehicles,
        "scenarios_stored": len(store.list_ids()),
    }


@app.get("/vehicles", tags=["справочники"])
def list_vehicles() -> dict[str, Any]:
    """Каталог техники со всеми пробелами ТТХ.

    Пробелы отдаются наружу намеренно: интерфейс обязан показывать, какие
    числа плана выведены допущением, а какие взяты из документа (инвариант 6).
    """
    try:
        catalog = load_catalog()
    except CoreError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"vehicles": [vehicle.model_dump(mode="json") for vehicle in catalog]}


@app.get("/assumptions", tags=["справочники"])
def list_assumptions(team_only: bool = False) -> dict[str, Any]:
    """Реестр допущений — основа раздела «известные ограничения» (E13)."""
    items = assumptions.team_assumptions() if team_only else assumptions.all_assumptions()
    return {
        "assumptions": [
            {
                "name": item.name,
                "value": item.value,
                "unit": item.unit,
                "kind": item.kind.value,
                "source": item.source,
                "rationale": item.rationale,
            }
            for item in items
        ]
    }


@app.get("/scenarios", tags=["сценарии"])
def list_scenarios() -> dict[str, Any]:
    """Список сохранённых сценариев."""
    return {
        "scenarios": [
            {"id": scenario_id, "name": store.get(scenario_id).name}
            for scenario_id in store.list_ids()
        ]
    }


@app.post("/scenarios", tags=["сценарии"], status_code=201)
def create_scenario(scenario: Scenario) -> dict[str, Any]:
    """Сохранить сценарий.

    Валидация целиком лежит на контракте `core/schemas.py`: неизвестное поле
    или перепутанные местами широта и долгота дадут 422 без единой проверки
    в этом слое.
    """
    saved = store.save(scenario)
    return {"id": saved.id, "name": saved.name}


@app.get("/scenarios/{scenario_id}", tags=["сценарии"])
def get_scenario(scenario_id: str) -> Scenario:
    """Сценарий по идентификатору."""
    try:
        return store.get(scenario_id)
    except ScenarioError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/scenarios/{scenario_id}/plan", tags=["расчёт"], status_code=202)
async def start_planning(
    scenario_id: str,
    alpha: float | None = Query(default=None, ge=0.0, le=1.0),
    seed: int | None = Query(default=None),
) -> dict[str, Any]:
    """Поставить сценарий в расчёт.

    Args:
        scenario_id: идентификатор сохранённого сценария.
        alpha: вес makespan в целевой функции, 0..1. Перекрывает сценарий.
        seed: зерно ГПСЧ. Перекрывает сценарий.

    Returns:
        Идентификатор фоновой задачи для поллинга.
    """
    try:
        scenario = store.get(scenario_id)
    except ScenarioError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return jobs.submit(scenario, alpha=alpha, seed=seed).public()


@app.get("/jobs", tags=["расчёт"])
def list_jobs() -> dict[str, Any]:
    """Все задачи расчёта, новые первыми."""
    return {"jobs": [job.public() for job in jobs.all()]}


@app.get("/jobs/{job_id}", tags=["расчёт"])
def get_job(job_id: str) -> dict[str, Any]:
    """Статус задачи. Это и есть точка поллинга."""
    return _job_or_404(job_id).public()


@app.get("/jobs/{job_id}/result", tags=["расчёт"])
def get_job_result(job_id: str) -> PipelineResult:
    """Полный результат прогона: все стадии конвейера.

    Инспектор стадий на фронте (E11) кормится именно отсюда — промежуточные
    стадии нужны, чтобы было видно, где именно сломался план.
    """
    return _finished_job(_job_or_404(job_id)).run.result  # type: ignore[union-attr]


@app.get("/jobs/{job_id}/export", tags=["расчёт"])
def export_job(
    job_id: str,
    export_format: ExportFormat = Query(default=ExportFormat.GEOJSON, alias="format"),
) -> Response:
    """Отдать полётное задание файлом.

    Args:
        job_id: идентификатор задачи.
        export_format: `geojson` или `kml`.

    Returns:
        Файл с заголовком вложения, готовый к сохранению.
    """
    job = _finished_job(_job_or_404(job_id))
    run = job.run
    assert run is not None

    if export_format is ExportFormat.KML:
        buffer = io.BytesIO()
        mission_to_kml(run.mission, run.scenario, run.work_area.projector).write(
            buffer, encoding="utf-8", xml_declaration=True
        )
        return Response(
            content=buffer.getvalue(),
            media_type="application/vnd.google-earth.kml+xml",
            headers={"Content-Disposition": f'attachment; filename="{run.scenario.id}.kml"'},
        )

    document = mission_to_geojson(run.mission, run.scenario, run.work_area.projector)
    return Response(
        content=json.dumps(document, ensure_ascii=False, indent=2),
        media_type="application/geo+json",
        headers={"Content-Disposition": f'attachment; filename="{run.scenario.id}.geojson"'},
    )
