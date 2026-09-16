"""Общие фикстуры тестов."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.catalog import VehicleCatalog, load_catalog
from core.cli import load_scenario
from core.schemas import Scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPO_ROOT / "scenarios"

#: Все тестовые сценарии. AGENT.md §6: прогон обязателен на ВСЕХ, включая «злой».
SCENARIO_PATHS = sorted(SCENARIOS_DIR.glob("*.json"))


@pytest.fixture(scope="session")
def catalog() -> VehicleCatalog:
    """Каталог техники из data/vehicles.yaml."""
    return load_catalog()


@pytest.fixture(params=[path.stem for path in SCENARIO_PATHS])
def scenario(request: pytest.FixtureRequest) -> Scenario:
    """Каждый тестовый сценарий по очереди."""
    return load_scenario(SCENARIOS_DIR / f"{request.param}.json")


@pytest.fixture
def simple_scenario() -> Scenario:
    """Простой сценарий — для быстрых проверок."""
    return load_scenario(SCENARIOS_DIR / "simple.json")
