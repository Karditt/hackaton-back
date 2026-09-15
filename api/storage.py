"""Хранилище сценариев: файлы JSON плюс индекс в памяти.

Бриф §3: файлы и/или SQLite, без обязательного PostGIS и без миграций.
Файлов достаточно — пространственных запросов поверх множества сценариев
проекту не нужно.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.errors import ScenarioError
from core.schemas import Scenario

#: Каталог хранилища. Переопределяется переменной окружения в Docker.
STORAGE_ENV_VAR = "GEOSCAN_STORAGE"
DEFAULT_STORAGE_DIR = Path(__file__).resolve().parents[1] / "storage"


class ScenarioStore:
    """Сценарии на диске с индексом в памяти."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or os.environ.get(STORAGE_ENV_VAR) or DEFAULT_STORAGE_DIR)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Scenario] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        """Прочитать сценарии, оставшиеся от прошлых запусков."""
        for path in sorted(self.directory.glob("*.json")):
            try:
                scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — битый файл не должен ронять сервис
                continue
            self._cache[scenario.id] = scenario

    def save(self, scenario: Scenario) -> Scenario:
        """Сохранить сценарий, перезаписав одноимённый.

        Args:
            scenario: сценарий.

        Returns:
            Сохранённый сценарий.
        """
        self._cache[scenario.id] = scenario
        (self.directory / f"{scenario.id}.json").write_text(
            scenario.model_dump_json(indent=2), encoding="utf-8"
        )
        return scenario

    def get(self, scenario_id: str) -> Scenario:
        """Сценарий по идентификатору.

        Raises:
            ScenarioError: сценария нет в хранилище.
        """
        try:
            return self._cache[scenario_id]
        except KeyError as exc:
            raise ScenarioError(f"Сценарий {scenario_id!r} не найден") from exc

    def list_ids(self) -> list[str]:
        """Идентификаторы всех сохранённых сценариев."""
        return sorted(self._cache)

    def seed_from(self, directory: Path) -> int:
        """Загрузить готовые сценарии из каталога (например, `scenarios/`).

        Нужно, чтобы на чистой машине после `docker compose up` в интерфейсе
        сразу было что показать, не рисуя полигон руками.

        Args:
            directory: каталог с JSON-сценариями.

        Returns:
            Число загруженных сценариев.
        """
        if not directory.is_dir():
            return 0
        loaded = 0
        for path in sorted(directory.glob("*.json")):
            try:
                scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            self._cache.setdefault(scenario.id, scenario)
            loaded += 1
        return loaded


__all__ = ["DEFAULT_STORAGE_DIR", "STORAGE_ENV_VAR", "ScenarioStore"]
