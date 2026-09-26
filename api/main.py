"""HTTP-интерфейс сервиса — основной интерфейс по ответу заказчика (№11).

КАРКАС E9. Сценарии (с проверкой на приёме: геометрия, пределы значений —
№19), запуск конвейера фоновой задачей, поллинг статуса, результат со всеми
стадиями, расписание, отдача файлов. Описание — OpenAPI по адресу `/docs`
(№20). Авторизации, ролей и многопользовательского режима нет — заказчик
подтвердил, что они не нужны (№15).

Ни одного расчёта в этом файле: хендлер валидирует запрос, зовёт `core` и
сериализует ответ.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import ValidationError

from api.jobs import Job, JobRegistry, JobState
from api.storage import ScenarioStore
from core import __version__, assumptions
from core.catalog import load_catalog
from core.errors import CoreError, ExportError, ScenarioError
from core.export.geojson import geojson_to_text, mission_to_geojson, scenario_to_geojson
from core.export.kml import kml_to_bytes, mission_to_kml
from core.export.package import file_stem, mission_zip
from core.export.schedule import schedule_to_csv
from core.geo.kml_import import import_layers
from core.pipeline import check_scenario
from core.schemas import ExportFormat, ImportReport, OptimizationMode, PipelineResult, Scenario
from core.sources import ScenarioSources, import_report, load_sources

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Суффикс копии сценария с правками параметров (веб, `scenarioOptions.ts`).
#: Геометрия у копии та же, поэтому и исходные файлы — те же, что у исходного.
CUSTOM_SUFFIX = "--custom"

#: Тип содержимого исходного файла по расширению.
_MEDIA_TYPES = {".kml": "application/vnd.google-earth.kml+xml", ".json": "application/json"}

store = ScenarioStore()
jobs = JobRegistry()
sources: dict[str, ScenarioSources] = {}
_reports: dict[str, ImportReport] = {}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Подложить тестовые сценарии, чтобы на чистой машине было что показать.

    Кроме `scenarios/*.json` — сценарии манифеста исходников, в том числе
    собранный из файлов заказчика (`scenarios/real/`).
    """
    store.seed_from(REPO_ROOT / "scenarios")
    sources.clear()
    sources.update(load_sources())
    for item in sources.values():
        store.seed_file(item.scenario)
    yield


def _sources_for(scenario_id: str) -> ScenarioSources | None:
    """Исходные файлы сценария или его копии с правками параметров."""
    return sources.get(scenario_id) or sources.get(scenario_id.removesuffix(CUSTOM_SUFFIX))


app = FastAPI(
    lifespan=lifespan,
    title="GEOSCAN — планировщик беспилотных авиационных работ",
    version=__version__,
    description=(
        "Тонкий слой поверх ядра `core`. Расчёт идёт фоновой задачей внутри "
        "процесса, статус опрашивается поллингом. Итог расчёта — полный план "
        "или отчёт о невыполнимости с причинами и рекомендациями."
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
        "scenarios_rejected": len(store.rejected),
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
    """Реестр допущений — основа раздела «известные ограничения» (E13).

    `kind`: `measured` — из документа, `customer` — значение заказчика,
    `team_assumption` — решение команды, `derived` — выведено по формуле.
    """
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
    """Список сохранённых сценариев.

    `rejected` — файлы хранилища, которые не прочитались по контракту (например,
    сценарии контракта v1 без `altitude_corridor`), с причиной: они не
    пропускаются молча.

    `planning_available = false` — сценарий открыт для просмотра, расчёт из
    интерфейса не запускается; причина — в `planning_note`.
    """
    items = []
    for scenario_id in store.list_ids():
        origin = _sources_for(scenario_id)
        items.append(
            {
                "id": scenario_id,
                "name": store.get(scenario_id).name,
                "planning_available": origin.planning_available if origin else True,
                "planning_note": origin.planning_note if origin else None,
            }
        )
    return {"scenarios": items, "rejected": store.list_rejected()}


@app.post("/scenarios", tags=["сценарии"], status_code=201)
def create_scenario(scenario: Scenario) -> dict[str, Any]:
    """Сохранить сценарий.

    Форма проверяется контрактом `core/schemas.py` (неизвестное поле,
    перепутанные широта и долгота, больше 15 бортов — 422). Содержание — ядром
    на приёме (`core.pipeline.check_scenario`): самопересечение с указанием
    места, слишком большая область, перекрытие ниже минимума, неизвестный
    борт — тоже 422, а не сбой расчёта потом (заказчик, №19).
    """
    try:
        check_scenario(scenario)
    except CoreError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    saved = store.save(scenario)
    return {"id": saved.id, "name": saved.name}


@app.post("/scenarios/import", tags=["сценарии"], status_code=201)
async def import_scenario(
    params: str = Form(description="JSON с параметрами задания: флот, площадки, GSD, сроки"),
    area: UploadFile = File(description="KML области съёмки"),
    zones: UploadFile | None = File(default=None, description="KML зон ограничений"),
    obstacles: UploadFile | None = File(default=None, description="KML высотных препятствий"),
    terrain_min_m: float = Form(default=0.0, description="Минимальная отметка рельефа, м"),
    terrain_max_m: float = Form(default=0.0, description="Максимальная отметка рельефа, м"),
) -> dict[str, Any]:
    """Собрать сценарий из слоёв KML и параметров задания (E9, долг №29).

    Геометрия приходит файлами, подготовленными в ГИС: рисовать и править её у
    нас заказчик не просил и просил не делать (№8, №12). Правила чтения —
    `docs/input_formats.md`.

    Ответ несёт отчёт импорта: что прочитано, что починено, что выброшено и что
    отброшено отбором по области. Молчать об этом нельзя — план начинается с
    того, что мы прочитали (№4).
    """
    try:
        payload = json.loads(params)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=422, detail=f"Параметры задания не JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Параметры задания — объект JSON")

    async def _text(upload: UploadFile | None) -> str | None:
        if upload is None:
            return None
        raw = await upload.read()
        return raw.decode("utf-8-sig", errors="replace")

    try:
        layers = import_layers(
            area_source=await _text(area) or "",
            zones_source=await _text(zones),
            obstacles_source=await _text(obstacles),
            terrain_min_m=terrain_min_m,
            terrain_max_m=terrain_max_m,
        )
    except CoreError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    payload["area"] = layers.area.model_dump(mode="json")
    payload["no_fly_zones"] = [zone.model_dump(mode="json") for zone in layers.zones]
    payload["obstacles"] = [obstacle.model_dump(mode="json") for obstacle in layers.obstacles]
    try:
        scenario = Scenario.model_validate(payload)
        check_scenario(scenario)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    except CoreError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    saved = store.save(scenario)
    return {"id": saved.id, "name": saved.name, "report": layers.report.model_dump(mode="json")}


@app.get("/scenarios/{scenario_id}", tags=["сценарии"])
def get_scenario(scenario_id: str) -> Scenario:
    """Сценарий по идентификатору."""
    try:
        return store.get(scenario_id)
    except ScenarioError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/scenarios/{scenario_id}/layers", tags=["сценарии"])
def get_scenario_layers(scenario_id: str) -> Response:
    """Слои сценария в GeoJSON — исходные данные на карте до расчёта (E10).

    Область, запретные зоны, граница пространства, препятствия и площадки с
    тем же полем `kind`, что в выгрузке плана (docs/export_format.md §4.2). В
    свойствах коллекции — число объектов по видам и площадь области, км².
    """
    try:
        scenario = store.get(scenario_id)
    except ScenarioError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(
        content=geojson_to_text(scenario_to_geojson(scenario)),
        media_type="application/geo+json",
    )


def _report_for(origin: ScenarioSources) -> ImportReport:
    """Отчёт импорта сценария: сохранённый или посчитанный по файлам один раз."""
    if origin.scenario_id not in _reports:
        _reports[origin.scenario_id] = import_report(origin)
    return _reports[origin.scenario_id]


@app.get("/scenarios/{scenario_id}/files", tags=["сценарии"])
def get_scenario_files(scenario_id: str) -> dict[str, Any]:
    """Исходные файлы сценария по видам и что с ними сделал импорт (E10).

    Виды: `area`, `zones`, `obstacles` — слои KML в формате заказчика,
    `params` — параметры задания, `scenario` — собранный JSON. У слоёв —
    счётчики импорта: прочитано, принято, исправлено, выброшено, вне области.
    Сценарий, сохранённый без исходников (через `POST /scenarios`), отдаёт
    пустой список.
    """
    try:
        store.get(scenario_id)
    except ScenarioError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    origin = _sources_for(scenario_id)
    if origin is None:
        return {"scenario_id": scenario_id, "source_id": None, "files": [], "warnings": []}

    report = _report_for(origin)
    stats = {"area": report.area, "zones": report.zones, "obstacles": report.obstacles}
    files = [
        {
            "kind": kind,
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "stats": stats[kind].model_dump(mode="json") if kind in stats else None,
        }
        for kind, path in [*origin.files.items(), ("scenario", origin.scenario)]
    ]
    return {
        "scenario_id": scenario_id,
        "source_id": origin.scenario_id,
        "files": files,
        "area_km2": report.area_km2,
        "warnings": [warning.model_dump(mode="json") for warning in report.warnings],
    }


@app.get("/scenarios/{scenario_id}/files/{kind}", tags=["сценарии"])
def download_scenario_file(scenario_id: str, kind: str) -> FileResponse:
    """Скачать исходный файл сценария: `area`, `zones`, `obstacles`, `params`, `scenario`."""
    origin = _sources_for(scenario_id)
    files = {**origin.files, "scenario": origin.scenario} if origin else {}
    if kind not in files:
        raise HTTPException(
            status_code=404, detail=f"У сценария {scenario_id!r} нет исходного файла {kind!r}"
        )
    path = files[kind]
    return FileResponse(
        path, filename=path.name, media_type=_MEDIA_TYPES.get(path.suffix, "text/plain")
    )


@app.post("/scenarios/{scenario_id}/plan", tags=["расчёт"], status_code=202)
async def start_planning(
    scenario_id: str,
    mode: OptimizationMode | None = Query(default=None),
    alpha: float | None = Query(default=None, ge=0.0, le=1.0),
    seed: int | None = Query(default=None),
) -> dict[str, Any]:
    """Поставить сценарий в расчёт.

    Args:
        scenario_id: идентификатор сохранённого сценария.
        mode: режим оптимизации — минимум срока или минимум ресурса (№23).
            Перекрывает сценарий.
        alpha: вес срока в свёртке для фронта Парето, 0..1. Перекрывает сценарий.
        seed: зерно ГПСЧ. Перекрывает сценарий.

    Returns:
        Идентификатор фоновой задачи для поллинга.
    """
    try:
        scenario = store.get(scenario_id)
    except ScenarioError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return jobs.submit(scenario, mode=mode, alpha=alpha, seed=seed).public()


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


@app.get("/jobs/{job_id}/schedule", tags=["расчёт"])
def get_job_schedule(job_id: str) -> dict[str, Any]:
    """Расписание вылетов (заказчик, №31).

    У невыполнимого плана расписания нет: `entries` пуст, `status` —
    `infeasible`, причины — в `/jobs/{id}/result`.
    """
    run = _finished_job(_job_or_404(job_id)).run
    assert run is not None
    schedule = run.mission.schedule
    return {
        "status": run.mission.status.value,
        "start_time": schedule.start_time.isoformat() if schedule and schedule.start_time else None,
        "entries": [entry.model_dump(mode="json") for entry in schedule.entries]
        if schedule
        else [],
    }


def _attachment(name: str) -> dict[str, str]:
    """Заголовок вложения: браузер сохраняет ответ файлом с этим именем."""
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@app.get("/jobs/{job_id}/export", tags=["расчёт"])
def export_job(
    job_id: str,
    export_format: ExportFormat = Query(default=ExportFormat.GEOJSON, alias="format"),
    board: str | None = Query(
        default=None,
        description="Борт, например `geoscan-gemini_1` — его индивидуальное задание. "
        "Список бортов — свойство `boards` выгрузки GeoJSON",
    ),
) -> Response:
    """Отдать итог расчёта файлом: полётное задание или отчёт о невыполнимости.

    Формат файлов описан в docs/export_format.md (заказчик, №21).

    Args:
        job_id: идентификатор задачи.
        export_format: `geojson`, `kml` или `schedule` (CSV).
        board: идентификатор борта — задание одного борта; без него — весь план.

    Returns:
        Файл с заголовком вложения, готовый к сохранению. Неизвестный борт —
        404 со списком бортов плана.
    """
    job = _finished_job(_job_or_404(job_id))
    run = job.run
    assert run is not None
    projector = run.work_area.projector
    stem = file_stem(run.scenario.id, board)

    try:
        if export_format is ExportFormat.KML:
            return Response(
                content=kml_to_bytes(mission_to_kml(run.result, run.scenario, projector, board)),
                media_type="application/vnd.google-earth.kml+xml",
                headers=_attachment(f"{stem}.kml"),
            )
        if export_format is ExportFormat.SCHEDULE:
            return Response(
                content=schedule_to_csv(run.mission, run.scenario, board),
                media_type="text/csv; charset=utf-8",
                headers=_attachment(f"{stem}.schedule.csv"),
            )
        document = mission_to_geojson(run.result, run.scenario, projector, board)
    except ExportError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(
        content=geojson_to_text(document),
        media_type="application/geo+json",
        headers=_attachment(f"{stem}.geojson"),
    )


@app.get("/jobs/{job_id}/package", tags=["расчёт"])
def export_package(job_id: str) -> Response:
    """Весь комплект файлов расчёта одним архивом ZIP.

    Внутри — то же, что CLI пишет в каталог: обзорные GeoJSON и KML,
    расписание CSV и KML с индивидуальным заданием каждого борта
    (docs/export_format.md §1). У невыполнимого плана — только отчёт.
    """
    run = _finished_job(_job_or_404(job_id)).run
    assert run is not None
    return Response(
        content=mission_zip(run.result, run.scenario, run.work_area.projector),
        media_type="application/zip",
        headers=_attachment(f"{file_stem(run.scenario.id)}.zip"),
    )
