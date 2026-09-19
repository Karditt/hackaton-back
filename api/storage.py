"""Хранилище сценариев: файлы JSON плюс индекс в памяти.

Бриф §3: файлы и/или SQLite, без обязательного PostGIS и без миграций.
Файлов достаточно — пространственных запросов поверх множества сценариев
проекту не нужно.

Файл, который не читается по контракту (битый JSON, сценарий старого контракта
v1 без `altitude_corridor`), не роняет сервис, но и не пропускается молча:
причина попадает в журнал и в `rejected`, а API отдаёт её в `GET /scenarios`
(долг №28). Чинить такой файл автоматически нельзя — недостающие значения
пришлось бы придумать.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import ValidationError

from core.errors import ScenarioError
from core.schemas import Scenario

#: Каталог хранилища. Переопределяется переменной окружения в Docker.
STORAGE_ENV_VAR = "GEOSCAN_STORAGE"
DEFAULT_STORAGE_DIR = Path(__file__).resolve().parents[1] / "storage"

#: Сколько ошибок контракта показывать в причине отказа — остальные числом.
_ERRORS_SHOWN = 3

logger = logging.getLogger(__name__)


def describe_rejection(error: Exception) -> str:
    """Причина, по которой файл сценария не прочитан, — для человека.

    Args:
        error: ошибка разбора файла.

    Returns:
        Первые ошибки контракта с местом; для сценария контракта v1 — подсказка,
        какое поле добавить.
    """
    if not isinstance(error, ValidationError):
        return str(error)
    items = error.errors()
    parts = [
        f"{'.'.join(str(part) for part in item['loc']) or 'сценарий'}: {item['msg']}"
        for item in items[:_ERRORS_SHOWN]
    ]
    if len(items) > _ERRORS_SHOWN:
        parts.append(f"и ещё {len(items) - _ERRORS_SHOWN}")
    text = "; ".join(parts)
    if any(item["loc"] == ("altitude_corridor",) and item["type"] == "missing" for item in items):
        text += (
            ". Это сценарий контракта v1: добавьте поле "
            '"altitude_corridor": {"max_altitude_agl_m": …} — верхнюю границу высот над '
            "землёй (в примере заказчика — 700 м, №10)"
        )
    return text


class ScenarioStore:
    """Сценарии на диске с индексом в памяти."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or os.environ.get(STORAGE_ENV_VAR) or DEFAULT_STORAGE_DIR)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Scenario] = {}
        self.rejected: dict[str, str] = {}
        """Непрочитанные файлы: «каталог/имя» → причина."""
        self._load_existing()

    def _read(self, path: Path) -> Scenario | None:
        """Прочитать файл сценария; непрочитанный — записать в `rejected` и в журнал."""
        key = f"{path.parent.name}/{path.name}"
        try:
            scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:  # ValidationError — подкласс ValueError
            reason = describe_rejection(error)
            self.rejected[key] = reason
            logger.warning("Сценарий %s не прочитан: %s", key, reason)
            return None
        self.rejected.pop(key, None)
        return scenario

    def _load_existing(self) -> None:
        """Прочитать сценарии, оставшиеся от прошлых запусков."""
        for path in sorted(self.directory.glob("*.json")):
            scenario = self._read(path)
            if scenario is not None:
                self._cache[scenario.id] = scenario

    def save(self, scenario: Scenario) -> Scenario:
        """Сохранить сценарий, перезаписав одноимённый.

        Args:
            scenario: сценарий.

        Returns:
            Сохранённый сценарий.
        """
        self._cache[scenario.id] = scenario
        path = self.directory / f"{scenario.id}.json"
        path.write_text(scenario.model_dump_json(indent=2), encoding="utf-8")
        self.rejected.pop(f"{path.parent.name}/{path.name}", None)
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

    def list_rejected(self) -> list[dict[str, str]]:
        """Непрочитанные файлы сценариев с причинами, по имени файла."""
        return [{"file": key, "reason": self.rejected[key]} for key in sorted(self.rejected)]

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
            scenario = self._read(path)
            if scenario is None:
                continue
            self._cache.setdefault(scenario.id, scenario)
            loaded += 1
        return loaded


__all__ = ["DEFAULT_STORAGE_DIR", "STORAGE_ENV_VAR", "ScenarioStore", "describe_rejection"]
