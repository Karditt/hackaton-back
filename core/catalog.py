"""Каталог техники: чтение `data/vehicles.yaml` в модели `Vehicle` / `Payload`.

ИНВАРИАНТ 6 (PROJECT_BRIEF.md §5): технические характеристики БВС не
выдумываются. Загрузчик не «чинит» отсутствующие значения правдоподобными
числами — он:

1. переносит пробел (`gap:` в каталоге) в `Vehicle.gaps` дословно;
2. если без значения расчёт невозможен в принципе (крейсерская скорость,
   радиус разворота), подставляет ПРАВИЛО из `core.assumptions` и записывает
   имя поля в `Vehicle.derived_fields` — чтобы отличить ТТХ производителя
   от решения команды было можно механически, а не по памяти.

Формат записи каталога: `параметр: {value: ..., source: "..."}` либо
`параметр: {value: null, gap: "..."}`.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import yaml

from core import assumptions
from core.errors import CatalogError
from core.schemas import Payload, PayloadKind, Vehicle, VehicleType

#: Каталог по умолчанию — рядом с репозиторием, а не относительно рабочей папки.
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "vehicles.yaml"

#: Переменная окружения для подмены каталога (используется в тестах и в Docker).
CATALOG_ENV_VAR = "GEOSCAN_VEHICLES"

_MM_PER_UM = 1.0e-3


def _node(raw: Any) -> tuple[Any, str | None]:
    """Разобрать узел каталога.

    Args:
        raw: узел вида `{value: ..., source: ...}`, `{value: null, gap: ...}`
            либо голый скаляр.

    Returns:
        Пара «значение, текст пробела». Пробел — None, если значение есть.
    """
    if isinstance(raw, dict) and ("value" in raw or "gap" in raw):
        value = raw.get("value")
        gap = raw.get("gap")
        if value is None:
            return None, (gap or "значение отсутствует в источнике")
        return value, None
    return raw, None


def _value(node: Any) -> Any:
    """Значение узла каталога без текста пробела."""
    return _node(node)[0]


def _first(seq: Any, index: int) -> float | None:
    """Элемент списка по индексу, если это список нужной длины."""
    if isinstance(seq, (list, tuple)) and len(seq) > index:
        return float(seq[index])
    return None


def _megapixels(raw: dict[str, Any]) -> float | None:
    """Число мегапикселей кадра.

    Raises:
        CatalogError: в записи диапазон мегапикселей. Это две камеры под одним
            именем (например, Riebo R4 и R6) — их нужно разнести по разным
            записям, а не выбирать границу за пользователя.
    """
    value = raw.get("resolution_mp")
    if isinstance(value, (list, tuple)):
        raise CatalogError(
            f"Нагрузка {raw.get('id')!r}: диапазон мегапикселей {value} — "
            "разнесите камеры по отдельным записям"
        )
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _sensor_by_format(raw: dict[str, Any]) -> tuple[float, float] | None:
    """Размер матрицы по названию формата, мм × мм, если документ даёт только формат.

    Формат «полный кадр» — это определение размера (36 × 24 мм), а не
    допущение; остальные форматы без размеров не восстанавливаются.
    """
    if raw.get("sensor_format") == "full_frame":
        width_mm, height_mm = assumptions.FULL_FRAME_SENSOR_MM
        return float(width_mm), float(height_mm)
    return None


def _aspect_ratio(
    fov_h: float | None, fov_v: float | None, sensor_w: float | None, sensor_h: float | None
) -> float | None:
    """Соотношение сторон кадра «ширина / высота».

    По углам поля зрения: tg(θг/2) / tg(θв/2) — центральная проекция, в которой
    ширина кадра на местности пропорциональна тангенсу половины угла. Иначе по
    размерам матрицы.
    """
    if fov_h and fov_v:
        return math.tan(math.radians(fov_h) / 2.0) / math.tan(math.radians(fov_v) / 2.0)
    if sensor_w and sensor_h:
        return sensor_w / sensor_h
    return None


def _payload(raw: dict[str, Any]) -> Payload:
    """Собрать модель нагрузки из записи каталога.

    Размер матрицы восстанавливается из размера пикселя и разрешения, если
    он не указан напрямую: это тождество (мм = мкм × пиксели), а не допущение.
    Если документ называет только формат «полный кадр», размер берётся по
    определению формата и помечается в `derived_fields`.

    Размер кадра в пикселях, если документ даёт только мегапиксели, выводится
    как ширина = √(Мп · 10⁶ · k), высота = ширина / k, где k — соотношение
    сторон по углам поля зрения или по матрице. Такие поля перечисляются в
    `derived_fields` (инвариант 6: вывод по формуле, а не выдумка).

    Отвергнутые командой значения документа (ключ `rejected:` в каталоге —
    например, углы обзора Riebo, несовместимые с фокусным) не читаются.

    Args:
        raw: словарь одной записи `payloads:`.

    Returns:
        Модель нагрузки. Неизвестные оптические параметры остаются None и
        перечисляются в `gaps`.
    """
    resolution_px = raw.get("resolution_px")
    width_px = _first(resolution_px, 0)
    height_px = _first(resolution_px, 1)

    derived: list[str] = []
    gaps: list[str] = []

    sensor_mm = raw.get("sensor_mm")
    sensor_width_mm = _first(sensor_mm, 0)
    sensor_height_mm = _first(sensor_mm, 1)
    by_format = _sensor_by_format(raw)
    if sensor_width_mm is None and by_format is not None:
        sensor_width_mm, sensor_height_mm = by_format
        derived.extend(["sensor_width_mm", "sensor_height_mm"])

    fov_h = raw.get("fov_horizontal_deg")
    fov_v = raw.get("fov_vertical_deg")
    fov_h = float(fov_h) if fov_h is not None else None
    fov_v = float(fov_v) if fov_v is not None else None

    megapixels = _megapixels(raw)
    aspect = _aspect_ratio(fov_h, fov_v, sensor_width_mm, sensor_height_mm)
    if width_px is None and megapixels and aspect:
        width_px = float(round(math.sqrt(megapixels * 1.0e6 * aspect)))
        height_px = float(round(width_px / aspect))
        derived.extend(["image_width_px", "image_height_px"])

    pixel_size_um = raw.get("pixel_size_um")
    if sensor_width_mm is None and pixel_size_um and width_px and height_px:
        sensor_width_mm = width_px * float(pixel_size_um) * _MM_PER_UM
        sensor_height_mm = height_px * float(pixel_size_um) * _MM_PER_UM

    focal, focal_gap = _node(raw.get("focal_length_mm"))
    if isinstance(focal, (list, tuple)):
        # Зум-объектив: фиксированного фокусного нет. Для фотограмметрии это
        # пробел, а не параметр — см. payload_note у Геоскан 801.
        focal_length_mm = None
        focal_gap = (
            f"объектив с переменным фокусным расстоянием {focal} мм, "
            "съёмочный масштаб не фиксирован"
        )
    else:
        focal_length_mm = float(focal) if focal is not None else None

    if focal_gap:
        gaps.append(f"focal_length_mm: {focal_gap.strip()}")
    if fov_h is None and (focal_length_mm is None or sensor_width_mm is None):
        gaps.append(
            "нет ни угла поля зрения, ни пары «фокусное расстояние + размер матрицы» — "
            "высоту съёмки по GSD посчитать не из чего"
        )
    if width_px is None:
        gaps.append("разрешение в пикселях не указано в источнике")

    try:
        kind = PayloadKind(raw["kind"])
    except (KeyError, ValueError) as exc:
        raise CatalogError(f"Неизвестный класс нагрузки в каталоге: {raw.get('kind')!r}") from exc

    return Payload(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        kind=kind,
        source=raw.get("source", "источник не указан"),
        sensor_width_mm=sensor_width_mm,
        sensor_height_mm=sensor_height_mm,
        image_width_px=int(width_px) if width_px else None,
        image_height_px=int(height_px) if height_px else None,
        focal_length_mm=focal_length_mm,
        fov_horizontal_deg=fov_h,
        fov_vertical_deg=fov_v,
        mass_kg=_value(raw.get("mass_kg")),
        gaps=gaps,
        derived_fields=derived,
    )


def _cruise_speed(
    raw: dict[str, Any], kind: VehicleType, gaps: list[str], derived: list[str]
) -> float:
    """Крейсерская скорость борта, м/с.

    Если производитель называет крейсерскую скорость (Геоскан Lite, 201 Термо),
    берётся она. Иначе скорость выводится по правилу из `core.assumptions` и
    помечается как производная.

    Args:
        raw: запись борта в каталоге.
        kind: тип БВС.
        gaps: список пробелов борта, пополняется на месте.
        derived: список производных полей, пополняется на месте.

    Returns:
        Крейсерская воздушная скорость, м/с.
    """
    cruise, gap = _node(raw.get("cruise_speed_mps"))
    if cruise is not None:
        return float(cruise)
    if gap:
        gaps.append(f"cruise_speed_mps: {gap.strip()}")

    if kind is VehicleType.FIXED_WING:
        low = _value(raw.get("airspeed_min_mps"))
        high = _value(raw.get("airspeed_max_mps"))
        if low is None or high is None:
            raise CatalogError(
                f"Борт {raw.get('id')!r}: нет ни крейсерской скорости, "
                "ни диапазона воздушной скорости"
            )
        derived.append("cruise_speed_mps")
        return float(low) + (float(high) - float(low)) * (
            assumptions.FIXED_WING_CRUISE_FRACTION_OF_RANGE
        )

    top = _value(raw.get("max_horizontal_speed_mps"))
    if top is None:
        raise CatalogError(
            f"Борт {raw.get('id')!r}: нет ни крейсерской, ни максимальной горизонтальной скорости"
        )
    derived.append("cruise_speed_mps")
    return float(top) * assumptions.MULTIROTOR_CRUISE_FRACTION_OF_MAX


def _turn_radius(
    raw: dict[str, Any], kind: VehicleType, cruise_mps: float, gaps: list[str], derived: list[str]
) -> float:
    """Минимальный радиус разворота, метры.

    У Геоскан 201 параметра нет в предоставленной выдержке руководства.
    Закрывается выводом из скорости и допустимого крена
    (`assumptions.fixed_wing_turn_radius_m`), а не выдуманным числом. Нужен для
    ширины зоны манёвра и времени разворота.
    """
    radius, gap = _node(raw.get("turn_radius_m"))
    if radius is not None:
        return float(radius)
    if gap:
        gaps.append(f"turn_radius_m: {gap.strip()}")
    if kind is VehicleType.MULTIROTOR:
        return 0.0
    derived.append("turn_radius_m")
    return assumptions.fixed_wing_turn_radius_m(cruise_mps)


def _vehicle(raw: dict[str, Any]) -> Vehicle:
    """Собрать модель борта из записи каталога."""
    try:
        kind = VehicleType(raw["type"])
    except (KeyError, ValueError) as exc:
        raise CatalogError(f"Неизвестный тип БВС в каталоге: {raw.get('type')!r}") from exc

    gaps: list[str] = []
    derived: list[str] = []

    cruise_mps = _cruise_speed(raw, kind, gaps, derived)
    turn_radius_m = _turn_radius(raw, kind, cruise_mps, gaps, derived)

    endurance_s, endurance_gap = _node(raw.get("endurance_s"))
    if endurance_s is None:
        raise CatalogError(
            f"Борт {raw.get('id')!r}: нет продолжительности полёта ({endurance_gap})"
        )

    max_wind_mps, wind_gap = _node(raw.get("max_wind_mps"))
    if max_wind_mps is None:
        raise CatalogError(f"Борт {raw.get('id')!r}: нет допустимой скорости ветра ({wind_gap})")

    battery = raw.get("battery")
    energy_wh: float | None = None
    if isinstance(battery, dict):
        energy_value, energy_gap = _node(battery.get("energy_wh", battery))
        if isinstance(energy_value, (int, float)):
            energy_wh = float(energy_value)
        elif energy_gap:
            gaps.append(f"battery: {energy_gap.strip()}")
    if energy_wh is None and "battery" not in raw:
        gaps.append("battery: сведения об АКБ в источнике отсутствуют")

    for key in ("max_payload_mass_kg", "survey_area_per_sortie"):
        _, gap = _node(raw.get(key))
        if gap:
            gaps.append(f"{key}: {gap.strip()}")

    payloads = [_payload(item) for item in raw.get("payloads", [])]

    # Отсечка заряда — значение заказчика (№34), а не ТТХ производителя.
    derived.append("battery_reserve_fraction")

    return Vehicle(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        type=kind,
        cruise_speed_mps=cruise_mps,
        max_speed_mps=_value(raw.get("airspeed_max_mps"))
        or _value(raw.get("max_horizontal_speed_mps")),
        min_speed_mps=_value(raw.get("airspeed_min_mps")),
        endurance_s=float(endurance_s),
        max_wind_mps=float(max_wind_mps),
        turn_radius_m=turn_radius_m,
        min_altitude_agl_m=_value(raw.get("min_safe_altitude_m")),
        max_altitude_agl_m=_value(raw.get("max_altitude_agl_m")),
        max_range_m=_value(raw.get("max_range_m")),
        energy_wh=energy_wh,
        battery_reserve_fraction=assumptions.BATTERY_RESERVE_FRACTION,
        takeoff_mode=_value(raw.get("takeoff_mode")),
        landing_mode=_value(raw.get("landing_mode")),
        payloads=payloads,
        gaps=gaps,
        derived_fields=derived,
    )


class VehicleCatalog:
    """Загруженный каталог техники."""

    def __init__(self, vehicles: list[Vehicle], path: Path) -> None:
        self.path = path
        self._by_id = {vehicle.id: vehicle for vehicle in vehicles}
        if len(self._by_id) != len(vehicles):
            raise CatalogError("Идентификаторы бортов в каталоге не уникальны")

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self):
        return iter(self._by_id.values())

    @property
    def vehicles(self) -> list[Vehicle]:
        """Все борта каталога."""
        return list(self._by_id.values())

    def get(self, vehicle_id: str) -> Vehicle:
        """Борт по идентификатору.

        Raises:
            CatalogError: борта нет в каталоге. Это ошибка сценария, а не
                повод подставить «похожий» борт.
        """
        try:
            return self._by_id[vehicle_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._by_id))
            raise CatalogError(
                f"Борт {vehicle_id!r} отсутствует в каталоге {self.path.name}. Известны: {known}"
            ) from exc


def load_catalog(path: str | os.PathLike[str] | None = None) -> VehicleCatalog:
    """Прочитать каталог техники.

    Args:
        path: путь к YAML. None — переменная окружения `GEOSCAN_VEHICLES`,
            затем `data/vehicles.yaml` рядом с репозиторием.

    Returns:
        Каталог бортов.

    Raises:
        CatalogError: файла нет, он повреждён или в нём отсутствуют данные,
            без которых планирование невозможно.
    """
    resolved = Path(path or os.environ.get(CATALOG_ENV_VAR) or DEFAULT_CATALOG_PATH)
    if not resolved.is_file():
        raise CatalogError(f"Каталог техники не найден: {resolved}")

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogError(f"Каталог техники не разбирается: {resolved}") from exc

    if not isinstance(raw, dict) or "vehicles" not in raw:
        raise CatalogError(f"В каталоге {resolved} нет корневого ключа 'vehicles'")

    return VehicleCatalog([_vehicle(item) for item in raw["vehicles"]], resolved)


__all__ = ["CATALOG_ENV_VAR", "DEFAULT_CATALOG_PATH", "VehicleCatalog", "load_catalog"]
