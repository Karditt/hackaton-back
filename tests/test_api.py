"""API — основной интерфейс (№11): маршруты, фоновый расчёт, поллинг, приём.

Проверяется транспорт, а не физика — физика покрыта тестами ядра.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from api.main import app, store  # noqa: E402
from api.storage import ScenarioStore  # noqa: E402
from tests.conftest import SCENARIOS_DIR  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    """Клиент FastAPI с хранилищем во временном каталоге."""
    store.directory = Path(tmp_path_factory.mktemp("storage"))
    with fastapi_testclient.TestClient(app) as test_client:
        yield test_client


def _simple() -> dict:
    return json.loads((SCENARIOS_DIR / "simple.json").read_text(encoding="utf-8"))


def _wait_for_job(client, job_id: str, timeout_s: float = 300.0) -> dict:
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
    assert payload["vehicles_in_catalog"] == 6


def test_scenarios_are_seeded(client) -> None:
    """На чистой машине в интерфейсе сразу есть тестовые сценарии."""
    ids = {item["id"] for item in client.get("/scenarios").json()["scenarios"]}
    assert {"simple", "medium", "nasty", "demo_100km2"} <= ids


def test_unreadable_stored_scenarios_are_reported(tmp_path: Path, client) -> None:
    """Долг №28: сценарий старого контракта и битый файл не пропускаются молча."""
    old = _simple()
    old["id"] = "old-v1"
    del old["altitude_corridor"]
    (tmp_path / "old-v1.json").write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "good.json").write_text(json.dumps(_simple()), encoding="utf-8")

    fresh = ScenarioStore(tmp_path)
    assert fresh.list_ids() == ["simple"]
    reasons = {item["file"].split("/")[-1]: item["reason"] for item in fresh.list_rejected()}
    assert set(reasons) == {"old-v1.json", "broken.json"}
    assert "altitude_corridor" in reasons["old-v1.json"] and "v1" in reasons["old-v1.json"]

    listing = client.get("/scenarios").json()
    assert listing["rejected"] == store.list_rejected()
    assert client.get("/health").json()["scenarios_rejected"] == len(store.rejected)


def test_contract_violation_is_422(client) -> None:
    """Опечатка в поле — 422 от контракта, без единой проверки в слое API."""
    payload = _simple()
    payload["wind"]["direction_degrees"] = payload["wind"].pop("direction_deg")
    assert client.post("/scenarios", json=payload).status_code == 422


def test_self_intersecting_area_is_422_with_location(client) -> None:
    """№19: самопересечение отвергается на приёме, с местом, — а не падает в расчёте."""
    payload = _simple()
    payload["id"] = "bow-tie"
    payload["area"] = [[[30.30, 59.90], [30.33, 59.91], [30.33, 59.90], [30.30, 59.91]]]
    response = client.post("/scenarios", json=payload)
    assert response.status_code == 422
    assert "самопересечение" in response.json()["detail"]


def test_plan_runs_in_background_and_polls_to_done(client) -> None:
    """Сквозной путь API: сценарий → расчёт → статус → результат со статусом плана."""
    started = client.post("/scenarios/simple/plan", params={"mode": "min_resource"})
    assert started.status_code == 202
    finished = _wait_for_job(client, started.json()["id"])
    assert finished["state"] == "done", finished.get("error")
    assert finished["mission_status"] == "feasible"

    result = client.get(f"/jobs/{finished['id']}/result").json()
    assert result["mission"]["status"] == "feasible"
    assert result["mission"]["mode"] == "min_resource"
    assert result["mission"]["sorties"]
    assert result["decomposition"]["cells"]


def test_infeasible_plan_is_a_result_not_a_failure(client) -> None:
    """Невыполнимое задание — задача DONE со статусом infeasible, причинами и советами."""
    job_id = client.post("/scenarios/nasty/plan").json()["id"]
    finished = _wait_for_job(client, job_id)
    assert finished["state"] == "done"
    mission = client.get(f"/jobs/{job_id}/result").json()["mission"]
    assert mission["status"] == "infeasible"
    assert mission["reasons"] and mission["recommendations"]


def test_schedule_and_exports(client) -> None:
    """Расписание отдельным маршрутом; выгрузка — GeoJSON, KML и CSV (№21, №31)."""
    job_id = client.post("/scenarios/simple/plan").json()["id"]
    _wait_for_job(client, job_id)

    schedule = client.get(f"/jobs/{job_id}/schedule").json()
    assert schedule["status"] == "feasible"
    assert schedule["entries"]

    geojson = client.get(f"/jobs/{job_id}/export", params={"format": "geojson"})
    assert geojson.status_code == 200
    assert geojson.json()["type"] == "FeatureCollection"
    kml = client.get(f"/jobs/{job_id}/export", params={"format": "kml"})
    assert kml.text.lstrip().startswith("<?xml")
    csv = client.get(f"/jobs/{job_id}/export", params={"format": "schedule"})
    assert csv.text.startswith("sortie_id,")


def test_board_export_and_package(client) -> None:
    """ТЗ: задание на каждый борт — параметром `board` и архивом всего комплекта."""
    job_id = client.post("/scenarios/simple/plan").json()["id"]
    _wait_for_job(client, job_id)

    boards = client.get(f"/jobs/{job_id}/export").json()["properties"]["boards"]
    board = boards[0]["board_id"]
    kml = client.get(f"/jobs/{job_id}/export", params={"format": "kml", "board": board})
    assert kml.status_code == 200
    assert f"simple.{board}.kml" in kml.headers["content-disposition"]
    assert boards[0]["label"] in kml.text

    unknown = client.get(f"/jobs/{job_id}/export", params={"format": "kml", "board": "nope_1"})
    assert unknown.status_code == 404
    assert board in unknown.json()["detail"]

    package = client.get(f"/jobs/{job_id}/package")
    assert package.status_code == 200
    assert package.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = set(archive.namelist())
    assert {"simple.kml", "simple.geojson", "simple.schedule.csv"} <= names
    assert {f"simple.{item['board_id']}.kml" for item in boards} <= names


def test_unknown_scenario_is_404(client) -> None:
    """Несуществующий сценарий — 404, а не 500."""
    assert client.get("/scenarios/no-such").status_code == 404
    assert client.post("/scenarios/no-such/plan").status_code == 404


def test_result_before_completion_is_409(client) -> None:
    """Результат недосчитанной задачи — 409, а не пустой план."""
    job_id = client.post("/scenarios/medium/plan").json()["id"]
    assert client.get(f"/jobs/{job_id}/result").status_code in (200, 409)
    _wait_for_job(client, job_id)


def test_assumptions_endpoint_exposes_sources(client) -> None:
    """Реестр допущений доступен фронту: ограничения показываются пользователю."""
    items = client.get("/assumptions", params={"team_only": True}).json()["assumptions"]
    assert items
    assert all(item["kind"] == "team_assumption" for item in items)


def test_vehicles_endpoint_exposes_gaps(client) -> None:
    """Пробелы и выведенные поля ТТХ отдаются наружу, а не скрываются."""
    vehicles = {vehicle["id"]: vehicle for vehicle in client.get("/vehicles").json()["vehicles"]}
    assert vehicles["geoscan-801"]["gaps"]
    assert vehicles["geoscan-201"]["derived_fields"]
    thermal = next(
        payload for payload in vehicles["geoscan-201-t"]["payloads"] if payload["id"] == "vlm640-t"
    )
    assert thermal["focal_length_mm"] is None and thermal["gaps"]
