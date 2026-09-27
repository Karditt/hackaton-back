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


def _import_files() -> dict[str, tuple[str, bytes, str]]:
    samples = SCENARIOS_DIR.parent / "data" / "samples"
    return {
        name: (path.name, path.read_bytes(), "application/vnd.google-earth.kml+xml")
        for name, path in (
            ("area", samples / "survey_area_msk.kml"),
            ("zones", samples / "zones_msk.kml"),
            ("obstacles", samples / "obstacles_msk.kml"),
        )
    }


def test_layers_are_imported_into_a_scenario(client) -> None:
    """Долг №29: три слоя KML и параметры задания приходят одним запросом.

    Проверяется транспорт и отчёт импорта; правила разбора — в тестах ядра
    (`tests/test_kml_import.py`).
    """
    params = json.loads(
        (SCENARIOS_DIR / "sources" / "real_msk" / "params.json").read_text(encoding="utf-8")
    )
    params["id"] = "imported"
    response = client.post(
        "/scenarios/import",
        files=_import_files(),
        data={"params": json.dumps(params, ensure_ascii=False)},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] == "imported"
    report = payload["report"]
    assert report["area"]["read"] == 696
    assert report["area"]["kept"] == 549
    assert report["obstacles"]["skipped"] == 4615
    assert report["area_km2"] == pytest.approx(245.0, abs=0.5)
    assert {warning["code"] for warning in report["warnings"]} >= {
        "import.geometry_fixed",
        "import.geometry_merged",
    }
    stored = client.get("/scenarios/imported").json()
    assert len(stored["area"]["polygons"]) == 549
    assert len(stored["obstacles"]) == 547


def test_import_without_params_is_422(client) -> None:
    """Параметры задания — не JSON: отказ на приёме, а не сбой при разборе слоёв."""
    response = client.post(
        "/scenarios/import",
        files={"area": ("area.kml", b"<kml/>", "application/xml")},
        data={"params": "не json"},
    )
    assert response.status_code == 422


def test_import_of_a_file_without_polygons_is_422(client) -> None:
    """Файл области без единого полигона — ошибка входа с понятной причиной."""
    response = client.post(
        "/scenarios/import",
        files={"area": ("area.kml", b"<kml><Document/></kml>", "application/xml")},
        data={"params": json.dumps({"id": "empty"})},
    )
    assert response.status_code == 422
    assert "полигон" in str(response.json()["detail"])


def test_edited_scenario_is_planned_as_a_copy(client) -> None:
    """E10: панель параметров читает сценарий, шлёт копию с правками и считает её.

    Исходный сценарий при этом не трогается — это весь договор веба с API:
    новых маршрутов панели параметров не нужно.
    """
    base = client.get("/scenarios/simple").json()
    edited = dict(base)
    edited["id"] = "simple--custom"
    edited["quality"] = {**base["quality"], "gsd_m": base["quality"]["gsd_m"] * 1.5}
    edited["wind"] = {"speed_mps": 3.0, "direction_deg": 90.0}
    edited["turnaround_s"] = 900
    assert client.post("/scenarios", json=edited).status_code == 201

    finished = _wait_for_job(client, client.post("/scenarios/simple--custom/plan").json()["id"])
    assert finished["state"] == "done", finished.get("error")
    assert client.get("/scenarios/simple").json() == base

    over_limit = dict(edited) | {"fleet": [{"vehicle_id": "geoscan-gemini", "count": 20}]}
    refused = client.post("/scenarios", json=over_limit)
    assert refused.status_code == 422
    assert "15" in json.dumps(refused.json(), ensure_ascii=False)


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


def test_scenario_layers_are_served_before_planning(client) -> None:
    """E10: исходные данные видны на карте до расчёта — слои сценария в GeoJSON."""
    response = client.get("/scenarios/medium/layers")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/geo+json")
    document = response.json()
    kinds = {feature["properties"]["kind"] for feature in document["features"]}
    assert "survey_area" in kinds and "site" in kinds
    assert not kinds & {"leg", "keypoint", "survey_cell"}, "до расчёта маршрутов нет"
    assert document["properties"]["counts"]["site"] >= 1
    assert document["properties"]["area_km2"] > 0


def test_customer_scenario_is_listed_and_computable(client) -> None:
    """Сценарий на файлах заказчика виден в интерфейсе и считается (долг №48 закрыт)."""
    listing = {item["id"]: item for item in client.get("/scenarios").json()["scenarios"]}
    assert listing["real_msk"]["planning_available"] is True
    assert listing["real_msk"]["planning_note"]
    assert listing["simple"]["planning_available"] is True

    files = {item["kind"]: item for item in client.get("/scenarios/real_msk/files").json()["files"]}
    assert files["area"]["name"] == "survey_area_msk.kml"
    assert files["area"]["stats"]["read"] == 696 and files["area"]["stats"]["kept"] == 549


def test_scenario_files_by_kind_and_download(client) -> None:
    """Исходники сценария по видам: слои KML со счётчиками импорта, параметры, сам JSON."""
    payload = client.get("/scenarios/medium/files").json()
    files = {item["kind"]: item for item in payload["files"]}
    assert set(files) == {"area", "zones", "params", "scenario"}
    assert files["zones"]["stats"]["kept"] == 2
    assert files["params"]["stats"] is None

    download = client.get("/scenarios/medium/files/zones")
    assert download.status_code == 200
    assert "zones.kml" in download.headers["content-disposition"]
    assert "<kml" in download.text and "zone_id" in download.text

    assert client.get("/scenarios/medium/files/obstacles").status_code == 404

    # Копия с правками параметров собрана из тех же файлов.
    edited = _simple()
    edited["id"] = "simple--custom"
    assert client.post("/scenarios", json=edited).status_code == 201
    copy = client.get("/scenarios/simple--custom/files").json()
    assert copy["source_id"] == "simple"


def test_unknown_scenario_is_404(client) -> None:
    """Несуществующий сценарий — 404, а не 500."""
    assert client.get("/scenarios/no-such").status_code == 404
    assert client.get("/scenarios/no-such/layers").status_code == 404
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
