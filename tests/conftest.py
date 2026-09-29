"""Общие фикстуры тестов."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.catalog import VehicleCatalog, load_catalog
from core.cli import load_scenario
from core.pipeline import PlanningRun, run_pipeline
from core.schemas import Scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPO_ROOT / "scenarios"

#: Все тестовые сценарии. docs/dev/AGENT.md §6: прогон обязателен на ВСЕХ, включая «злой».
SCENARIO_PATHS = sorted(SCENARIOS_DIR.glob("*.json"))
SCENARIO_NAMES = [path.stem for path in SCENARIO_PATHS]

#: Сценарии, у которых по замыслу есть полный план.
FEASIBLE = {"simple", "medium", "demo_100km2"}

#: Сценарии, которые по замыслу невыполнимы — проверка отчёта о невыполнимости.
INFEASIBLE = {"nasty"}


@pytest.fixture(scope="session")
def catalog() -> VehicleCatalog:
    """Каталог техники из data/vehicles.yaml."""
    return load_catalog()


@pytest.fixture(scope="session")
def runs(catalog: VehicleCatalog) -> dict[str, PlanningRun]:
    """Каждый сценарий прогоняется один раз на сессию: демо-сценарий не мгновенный."""
    return {
        path.stem: run_pipeline(load_scenario(path), catalog=catalog) for path in SCENARIO_PATHS
    }


@pytest.fixture(params=SCENARIO_NAMES)
def scenario_name(request: pytest.FixtureRequest) -> str:
    """Имя каждого тестового сценария по очереди."""
    return request.param


@pytest.fixture
def scenario(scenario_name: str) -> Scenario:
    """Каждый тестовый сценарий по очереди."""
    return load_scenario(SCENARIOS_DIR / f"{scenario_name}.json")


@pytest.fixture
def run(runs: dict[str, PlanningRun], scenario_name: str) -> PlanningRun:
    """Прогон каждого тестового сценария по очереди."""
    return runs[scenario_name]


@pytest.fixture
def simple_scenario() -> Scenario:
    """Простой сценарий — для быстрых проверок."""
    return load_scenario(SCENARIOS_DIR / "simple.json")


def small_scenario(**overrides: Any) -> Scenario:
    """Маленький сценарий на Gemini для точечных проверок; поля можно заменить."""
    payload: dict[str, Any] = {
        "id": "small",
        "area": [[[30.300, 59.900], [30.320, 59.900], [30.320, 59.910], [30.300, 59.910]]],
        "altitude_corridor": {"max_altitude_agl_m": 700},
        "sites": [{"id": "base", "position": [30.310, 59.898]}],
        "quality": {"gsd_m": 0.05},
        "wind": {"speed_mps": 3.0, "direction_deg": 270.0},
        "fleet": [{"vehicle_id": "geoscan-gemini", "count": 1, "payload_id": "geoscan-pf1b"}],
    }
    payload.update(overrides)
    return Scenario.model_validate(payload)
