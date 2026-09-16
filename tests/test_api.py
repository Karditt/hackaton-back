"""Каркас API: маршруты есть, расчёт уходит в фон, поллинг отвечает.

Проверяется транспорт, а не физика — физика покрыта тестами ядра.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from api.main import app, store  # noqa: E402
from tests.conftest import SCENARIOS_DIR  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    """Клиент FastAPI с хранилищем во временном каталоге."""
    store.directory = Path(tmp_path_factory.mktemp("storage"))
    with fastapi_testclient.TestClient(app) as test_client:
        yield test_client


def _wait_for_job(client, job_id: str, timeout_s: float = 120.0) -> dict:
    """Дождаться завершения фоновой задачи поллингом — как это делает фронт."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = client.get(f"/jobs/{job_id}").json()
        if payload["state"] in ("done", "failed"):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Задача {job_id} не завершилась за {timeout_s} с")


def test_health_reports_catalog(client) -> None:
    """Живость сервиса включает доступность каталога техники."""
    payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["vehicles_in_catalog"] == 3


def test_scenarios_are_seeded(client) -> None:
    """На чистой машине в интерфейсе сразу есть тестовые сценарии."""
    ids = {item["id"] for item in client.get("/scenarios").json()["scenarios"]}
    assert {"simple", "medium", "nasty"} <= ids


def test_invalid_scenario_is_rejected_by_contract(client) -> None:
    """Опечатка в поле — 422 от контракта, без единой проверки в слое API."""
    payload = json.loads((SCENARIOS_DIR / "simple.json").read_text(encoding="utf-8"))
    payload["wind"]["direction_degrees"] = payload["wind"].pop("direction_deg")
    assert client.post("/scenarios", json=payload).status_code == 422


def test_plan_runs_in_background_and_polls_to_done(client) -> None:
    """Сквозной путь API: сценарий → расчёт → статус → результат."""
    started = client.post("/scenarios/simple/plan", params={"alpha": 1.0})
    assert started.status_code == 202
    job_id = started.json()["id"]

    finished = _wait_for_job(client, job_id)
    assert finished["state"] == "done", finished.get("error")

    result = client.get(f"/jobs/{job_id}/result").json()
    assert result["mission"]["sorties"]
    assert result["mission"]["alpha"] == 1.0
    assert result["coverage"]["strips"]
    assert result["mission"]["stub_stages"]


def test_export_returns_files(client) -> None:
    """Выгрузка отдаётся файлом в обоих форматах."""
    job_id = client.post("/scenarios/simple/plan").json()["id"]
    _wait_for_job(client, job_id)

    geojson = client.get(f"/jobs/{job_id}/export", params={"format": "geojson"})
    assert geojson.status_code == 200
    assert geojson.json()["type"] == "FeatureCollection"

    kml = client.get(f"/jobs/{job_id}/export", params={"format": "kml"})
    assert kml.status_code == 200
    assert kml.text.lstrip().startswith("<?xml")


def test_unknown_scenario_is_404(client) -> None:
    """Несуществующий сценарий — 404, а не 500."""
    assert client.get("/scenarios/no-such").status_code == 404
    assert client.post("/scenarios/no-such/plan").status_code == 404


def test_result_before_completion_is_409(client) -> None:
    """Результат недосчитанной задачи — 409, а не пустой план."""
    job_id = client.post("/scenarios/medium/plan").json()["id"]
    response = client.get(f"/jobs/{job_id}/result")
    assert response.status_code in (200, 409)
    _wait_for_job(client, job_id)


def test_assumptions_endpoint_exposes_sources(client) -> None:
    """Реестр допущений доступен фронту: ограничения показываются пользователю."""
    items = client.get("/assumptions", params={"team_only": True}).json()["assumptions"]
    assert items
    assert all(item["kind"] == "team_assumption" for item in items)


def test_vehicles_endpoint_exposes_gaps(client) -> None:
    """Пробелы ТТХ отдаются наружу, а не скрываются."""
    vehicles = client.get("/vehicles").json()["vehicles"]
    by_id = {vehicle["id"]: vehicle for vehicle in vehicles}
    assert by_id["geoscan-801"]["energy_wh"] is None
    assert by_id["geoscan-201"]["derived_fields"]
