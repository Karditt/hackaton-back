"""Общий контракт данных: pydantic-модели всех стадий конвейера.

ЭТО ЕДИНСТВЕННАЯ ТОЧКА СИНХРОНИЗАЦИИ между `core`, `api` и `web`
(инвариант 3, PROJECT_BRIEF.md §5). Правка ломает три параллельных потока
работы сразу, поэтому порядок изменения зафиксирован в AGENT.md §4:
аддитивно → перегенерировать TS-типы → отметить в docs/current_state.md.

Версия 2 — по ответам заказчика (docs/customer_qa.md): мультиполигон области,
препятствия, коридор высот, зоны с высотами и окнами времени, декомпозиция на
подобласти, раздельные площадки взлёта и посадки, расписание, статус плана
«выполним / невыполним» с причинами и рекомендациями.

Две системы координат и почему их две
-------------------------------------
Инвариант 1: вся геометрия считается в МЕТРИЧЕСКОЙ проекции, WGS84 живёт
только на границах системы. Чтобы это нельзя было нарушить незаметно,
географические и метрические координаты — РАЗНЫЕ ТИПЫ:

* `GeoPoint` / `GeoPolygon` / `GeoMultiPolygon` — WGS84, градусы. Только
  `Scenario` (вход) и экспорт (выход). Внутри конвейера их быть не должно.
* `MetricPoint` / `MetricPolygon` / `MetricLineString` / `Waypoint` — метры
  в рабочей проекции `CrsInfo`. Всё, что между стадиями.

Подставить одно вместо другого не получится: pydantic отвергнет по именам
полей (`lon`/`lat` против `x_m`/`y_m`).

Единицы измерения — в именах полей
----------------------------------
Суффикс поля называет единицу: `_m` метры, `_s` секунды, `_mps` м/с,
`_deg` градусы, `_m2` квадратные метры. Доли (0..1) — суффикс `_fraction`
или имя `overlap`. Поле без суффикса — не число (идентификатор, перечисление,
геометрия). Все высоты — НАД ПОВЕРХНОСТЬЮ (AGL): их держит автопилот
(заказчик, №10).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core import assumptions

# --------------------------------------------------------------------------
# Перечисления
# --------------------------------------------------------------------------


class VehicleType(str, Enum):
    """Тип БВС. Определяет кинематику разворота и минимум разделения."""

    FIXED_WING = "fixed_wing"
    MULTIROTOR = "multirotor"


class SurveyType(str, Enum):
    """Тип съёмки. Перекрытия по типам — `data/survey_presets.yaml` (заказчик, №25)."""

    RGB = "rgb"
    MULTISPECTRAL = "multispectral"
    IR = "ir"
    LIDAR = "lidar"
    GEOPHYSICAL = "geophysical"


class PayloadKind(str, Enum):
    """Класс полезной нагрузки. Основа матрицы совместимости «борт × нагрузка» (E2)."""

    RGB = "rgb"
    MULTISPECTRAL = "multispectral"
    IR = "ir"
    LIDAR = "lidar"
    GEOPHYSICAL = "geophysical"
    INSPECTION_RGB = "inspection_rgb"  # зум-камера на подвесе: не для площадной съёмки
    GIMBAL = "gimbal"  # не сенсор, а носитель сенсора


class FlightPhase(str, Enum):
    """Фаза полёта. Инвариант 7: время вылета — сумма фаз."""

    TAKEOFF = "takeoff"
    CLIMB = "climb"
    TRANSIT = "transit"
    STRIP = "strip"
    TURN = "turn"
    HOLD = "hold"  # ожидание: самолёт — круг, мультиротор — зависание (заказчик, №37)
    DESCENT = "descent"
    LANDING = "landing"


class SiteKind(str, Enum):
    """Назначение наземной площадки."""

    PRIMARY = "primary"  # штатный взлёт и посадка
    RESERVE = "reserve"  # внештатная посадка и зона ожидания, не для работы (заказчик, №41)


class Severity(str, Enum):
    """Тяжесть предупреждения."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Stage(str, Enum):
    """Стадия конвейера. Нужна, чтобы предупреждение было адресным."""

    GEO = "geo"
    SURVEY = "survey"
    DECOMPOSE = "decompose"
    COVERAGE = "coverage"
    FLIGHT = "flight"
    TRANSIT = "transit"
    ALLOCATE = "allocate"
    SCHEDULE = "schedule"
    VALIDATE = "validate"
    ADVISE = "advise"
    EXPORT = "export"


class OptimizationMode(str, Enum):
    """Режим оптимизации (заказчик, №23; PROJECT_BRIEF.md §4.3)."""

    MIN_MAKESPAN = "min_makespan"  # кратчайший срок выполнения всех работ
    MIN_RESOURCE = "min_resource"  # минимум ресурса техники при необязательном дедлайне


class MissionStatus(str, Enum):
    """Итог конвейера. Частичных планов нет (инвариант 10)."""

    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"


class RecommendationKind(str, Enum):
    """Что предложить пользователю, если задание невыполнимо (заказчик, №33, №42)."""

    ADD_SITE = "add_site"
    ADD_VEHICLE = "add_vehicle"
    ADD_BATTERIES = "add_batteries"
    OTHER_MODEL = "other_model"
    WAIT = "wait"
    RELAX_REQUIREMENT = "relax_requirement"


class ExportFormat(str, Enum):
    """Формат выгрузки."""

    GEOJSON = "geojson"
    KML = "kml"
    SCHEDULE = "schedule"  # расписание вылетов, CSV


class AltitudeMode(str, Enum):
    """Режим высоты в экспорте.

    Критично для KML: три режима дают три РАЗНЫХ смысла одной и той же цифры
    (PROJECT_BRIEF.md §7). Высоты проекта — над поверхностью (AGL), что
    соответствует `relativeToGround`.
    """

    CLAMP_TO_GROUND = "clampToGround"
    RELATIVE_TO_GROUND = "relativeToGround"
    ABSOLUTE = "absolute"


# --------------------------------------------------------------------------
# Базовая модель
# --------------------------------------------------------------------------


class Model(BaseModel):
    """Общая база: запрещает неизвестные поля.

    Опечатка в имени поля сценария должна падать с ошибкой, а не молча
    игнорироваться и давать «план по умолчанию».
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=False)


def _nesting_depth(value: Any) -> int:
    """Глубина вложенности списков до первого скаляра."""
    depth = 0
    while isinstance(value, (list, tuple)) and value:
        depth += 1
        value = value[0]
    return depth


# --------------------------------------------------------------------------
# Геометрия: WGS84 (границы системы)
# --------------------------------------------------------------------------


class GeoPoint(Model):
    """Точка в WGS84 (EPSG:4326), градусы.

    Допускается как на входе сценария, так и в экспорте — и больше нигде.
    Принимает компактную запись `[lon, lat]` или `[lon, lat, alt_m]`
    (порядок как в GeoJSON, RFC 7946: сначала долгота).
    """

    lon: float = Field(ge=-180.0, le=180.0, description="Долгота, градусы WGS84")
    lat: float = Field(ge=-90.0, le=90.0, description="Широта, градусы WGS84")
    alt_m: float | None = Field(default=None, description="Высота над поверхностью, метры")

    @model_validator(mode="before")
    @classmethod
    def _accept_sequence(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            if len(value) == 2:
                return {"lon": value[0], "lat": value[1]}
            if len(value) == 3:
                return {"lon": value[0], "lat": value[1], "alt_m": value[2]}
            raise ValueError("Точка WGS84 записывается как [lon, lat] или [lon, lat, alt_m]")
        if isinstance(value, dict) and value.get("type") == "Point":
            return cls._accept_sequence(value.get("coordinates"))
        return value


class GeoPolygon(Model):
    """Полигон в WGS84: внешний контур и произвольное число отверстий.

    Кольца не требуется замыкать — замыкание доопределяется при переводе в
    shapely. Принимает координаты GeoJSON Polygon `[[[lon, lat], ...], ...]`
    (первое кольцо — внешнее) и геометрию GeoJSON `{"type": "Polygon", ...}`.
    Самопересечения отвергаются на приёме (`core.geo`), а не чинятся молча
    (заказчик, №9, №19).
    """

    outer: list[GeoPoint] = Field(min_length=3, description="Внешний контур, WGS84")
    holes: list[list[GeoPoint]] = Field(default_factory=list, description="Отверстия, WGS84")

    @model_validator(mode="before")
    @classmethod
    def _accept_rings(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("type") == "Polygon":
            value = value.get("coordinates")
        if isinstance(value, (list, tuple)):
            rings = list(value)
            if not rings:
                raise ValueError("Полигон должен содержать хотя бы внешнее кольцо")
            return {"outer": rings[0], "holes": rings[1:]}
        return value


class GeoMultiPolygon(Model):
    """Мультиполигон в WGS84 — форма области съёмки (заказчик, №9).

    Принимает: координаты GeoJSON Polygon (одна часть), координаты GeoJSON
    MultiPolygon (несколько частей), геометрию GeoJSON с полем `type` или
    явную форму `{"polygons": [...]}`.
    """

    polygons: list[GeoPolygon] = Field(min_length=1, description="Части мультиполигона")

    @model_validator(mode="before")
    @classmethod
    def _accept_geojson(cls, value: Any) -> Any:
        if isinstance(value, dict) and "type" in value:
            kind = value.get("type")
            coordinates = value.get("coordinates")
            if kind == "Polygon":
                return {"polygons": [coordinates]}
            if kind == "MultiPolygon":
                return {"polygons": list(coordinates or [])}
            raise ValueError(f"Область съёмки — Polygon или MultiPolygon, получено {kind!r}")
        if isinstance(value, (list, tuple)):
            depth = _nesting_depth(value)
            if depth == 3:
                return {"polygons": [value]}
            if depth == 4:
                return {"polygons": list(value)}
            if value and isinstance(value[0], dict):
                return {"polygons": list(value)}
            raise ValueError(
                "Ожидались координаты GeoJSON Polygon (три уровня вложенности) "
                "или MultiPolygon (четыре уровня)"
            )
        return value


# --------------------------------------------------------------------------
# Геометрия: метрическая проекция (всё, что внутри конвейера)
# --------------------------------------------------------------------------


class CrsInfo(Model):
    """Рабочая метрическая проекция, выбранная ядром под конкретный сценарий.

    Сопровождает КАЖДУЮ стадию: без неё метры не интерпретируются обратно
    в координаты и экспорт невозможен.
    """

    epsg: int = Field(description="Код EPSG метрической проекции, например 32636")
    name: str = Field(description="Человекочитаемое имя, например «UTM zone 36N»")
    unit: str = Field(default="m", description="Единица проекции, всегда метры")


class MetricPoint(Model):
    """Точка в рабочей метрической проекции, метры."""

    x_m: float
    y_m: float

    @model_validator(mode="before")
    @classmethod
    def _accept_sequence(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return {"x_m": value[0], "y_m": value[1]}
        return value


class MetricLineString(Model):
    """Ломаная в метрической проекции. Минимум две точки."""

    points: list[MetricPoint] = Field(min_length=2)

    @model_validator(mode="before")
    @classmethod
    def _accept_sequence(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return {"points": list(value)}
        return value


class MetricPolygon(Model):
    """Полигон в метрической проекции: внешний контур и отверстия."""

    outer: list[MetricPoint] = Field(min_length=3)
    holes: list[list[MetricPoint]] = Field(default_factory=list)


class Waypoint(Model):
    """Точка маршрута: плановое положение, высота, путевая скорость, время.

    Высота — НАД ПОВЕРХНОСТЬЮ (AGL), её держит автопилот (заказчик, №10).
    Именно это значение уходит в KML с режимом `relativeToGround`.
    """

    x_m: float = Field(description="Координата X рабочей проекции, метры")
    y_m: float = Field(description="Координата Y рабочей проекции, метры")
    alt_m: float = Field(description="Высота над поверхностью (AGL), метры")
    ground_speed_mps: float | None = Field(
        default=None, description="Путевая скорость на подходе к точке, м/с"
    )
    time_s: float = Field(default=0.0, description="Момент прохождения от начала работ, секунды")


# --------------------------------------------------------------------------
# Предупреждения
# --------------------------------------------------------------------------


class PlanWarning(Model):
    """Предупреждение или причина невыполнимости.

    AGENT.md §8: невыполнимый сценарий — это отчёт с причиной, а не тихий
    пустой результат. Заглушки стадий обязаны сообщать о себе здесь, иначе они
    «молча выглядят как настоящий расчёт».
    """

    code: str = Field(description="Машинный код, например 'validate.nfz'")
    severity: Severity = Severity.WARNING
    stage: Stage
    message: str = Field(description="Человекочитаемое объяснение")
    context: dict[str, str] = Field(
        default_factory=dict, description="Идентификаторы: sortie_id, vehicle_id, cell_id и т.п."
    )
    geometry: MetricLineString | None = Field(
        default=None, description="Проблемный участок для подсветки на карте (E11)"
    )


# --------------------------------------------------------------------------
# Техника: каталог из data/vehicles.yaml
# --------------------------------------------------------------------------


class Payload(Model):
    """Полезная нагрузка.

    Инвариант 6: ни одно число не выдумывается. Оптические параметры, которых
    нет в официальном документе, остаются `None` и попадают в `gaps`. Параметры,
    выведенные из документированных чисел по формуле, перечислены в
    `derived_fields`.
    """

    id: str
    name: str
    kind: PayloadKind
    source: str = Field(description="Документ и страница — обязательно (инвариант 6)")

    sensor_width_mm: float | None = None
    sensor_height_mm: float | None = None
    image_width_px: int | None = None
    image_height_px: int | None = None
    focal_length_mm: float | None = None
    fov_horizontal_deg: float | None = Field(
        default=None, description="Угол поля зрения по ширине кадра, градусы"
    )
    fov_vertical_deg: float | None = Field(
        default=None, description="Угол поля зрения по высоте кадра, градусы"
    )
    min_shot_interval_s: float | None = Field(
        default=None, description="Минимальный интервал между кадрами, секунды"
    )
    mass_kg: float | None = None
    gaps: list[str] = Field(
        default_factory=list, description="Известные пробелы ТТХ, дословно из каталога"
    )
    derived_fields: list[str] = Field(
        default_factory=list, description="Поля, выведенные по формуле, а не взятые из документа"
    )


class Vehicle(Model):
    """Борт.

    Значения приходят из `data/vehicles.yaml`. Там, где производитель параметр
    не публикует, подставляется допущение из `core.assumptions` — и такая
    подстановка ОБЯЗАТЕЛЬНО перечислена в `derived_fields`, чтобы отличать
    ТТХ от решения команды.
    """

    id: str
    name: str
    type: VehicleType

    cruise_speed_mps: float = Field(gt=0, description="Крейсерская воздушная скорость, м/с")
    max_speed_mps: float | None = Field(default=None, description="Максимальная скорость, м/с")
    min_speed_mps: float | None = Field(
        default=None, description="Минимальная воздушная скорость, м/с (самолётный тип)"
    )
    endurance_s: float = Field(gt=0, description="Максимальная продолжительность полёта, секунды")
    max_wind_mps: float = Field(gt=0, description="Допустимая скорость ветра, м/с")
    turn_radius_m: float = Field(
        ge=0, description="Минимальный радиус разворота, метры (0 — мультиротор)"
    )

    min_altitude_agl_m: float | None = Field(default=None, description="Мин. безопасная высота, м")
    max_altitude_agl_m: float | None = Field(default=None, description="Потолок над землёй, м")
    max_range_m: float | None = Field(default=None, description="Протяжённость маршрута, метры")

    energy_wh: float | None = Field(default=None, description="Энергия АКБ, Вт·ч (справочно)")
    battery_reserve_fraction: float = Field(
        ge=0.0, lt=1.0, description="Отсечка заряда, доля 0..1 (заказчик, №34)"
    )

    takeoff_mode: str | None = None
    landing_mode: str | None = None

    payloads: list[Payload] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list, description="Пробелы ТТХ из каталога (инвариант 6)"
    )
    derived_fields: list[str] = Field(
        default_factory=list,
        description="Поля, заполненные допущением команды, а не ТТХ производителя",
    )
    user_fields: list[str] = Field(
        default_factory=list,
        description="Поля, заданные пользователем, а не взятые из каталога data/vehicles.yaml",
    )

    def payload(self, payload_id: str) -> Payload | None:
        """Нагрузка по идентификатору, либо None."""
        for item in self.payloads:
            if item.id == payload_id:
                return item
        return None

    def is_fixed_wing(self) -> bool:
        """True — борт самолётного типа."""
        return self.type is VehicleType.FIXED_WING


# --------------------------------------------------------------------------
# Вход конвейера: Scenario (единственное место, где живёт WGS84 на входе)
# --------------------------------------------------------------------------


class Wind(Model):
    """Ветер.

    ИНВАРИАНТ 4. `direction_deg` — МЕТЕОРОЛОГИЧЕСКАЯ конвенция: направление,
    ОТКУДА дует. 0° — северный ветер (дует с севера на юг), 90° — восточный.
    Перевод в вектор — `core.flight.wind.wind_vector`, и только там.

    Роль ветра (заказчик, №45): выполнимость — удержит ли борт путевую
    скорость — и направление галсов. Во время и расход ветер не входит.
    """

    speed_mps: float = Field(ge=0.0, description="Скорость ветра, м/с")
    direction_deg: float = Field(
        ge=0.0, le=360.0, description="Откуда дует, градусы от севера по часовой стрелке"
    )


class Site(Model):
    """Наземная площадка взлёта-посадки, WGS84. Вместимость не ограничена (№28)."""

    id: str
    name: str = ""
    position: GeoPoint
    kind: SiteKind = SiteKind.PRIMARY


class NoFlyZone(Model):
    """Запретная зона, WGS84: полигон, высотный диапазон и окно действия.

    Заказчик, №40: зоны ОРВД — пространственно-временные, бывают постоянные
    (аэродром) и временные. Высоты — над поверхностью. Зона без высот
    запретна на всех высотах; зона без окна действует всегда.
    """

    id: str
    name: str = ""
    polygon: GeoPolygon
    min_alt_m: float | None = Field(default=None, ge=0, description="Нижняя граница, м AGL")
    max_alt_m: float | None = Field(default=None, gt=0, description="Верхняя граница, м AGL")
    active_from: datetime | None = Field(default=None, description="Начало действия, UTC")
    active_to: datetime | None = Field(default=None, description="Конец действия, UTC")

    @model_validator(mode="after")
    def _ranges_are_ordered(self) -> NoFlyZone:
        if (
            self.min_alt_m is not None
            and self.max_alt_m is not None
            and self.min_alt_m >= self.max_alt_m
        ):
            raise ValueError(f"Зона {self.id}: нижняя граница не ниже верхней")
        if (
            self.active_from is not None
            and self.active_to is not None
            and self.active_from >= self.active_to
        ):
            raise ValueError(f"Зона {self.id}: окно действия пустое")
        return self

    def is_permanent(self) -> bool:
        """True — зона без окна действия."""
        return self.active_from is None and self.active_to is None


class Obstacle(Model):
    """Высотное препятствие — отдельный входной слой (заказчик, №2, №44).

    Геометрия — точка ИЛИ полигон. Высота — над землёй.
    """

    id: str
    name: str = ""
    position: GeoPoint | None = Field(default=None, description="Точечное препятствие, WGS84")
    polygon: GeoPolygon | None = Field(default=None, description="Площадное препятствие, WGS84")
    height_m: float = Field(gt=0, description="Высота над землёй, метры")

    @model_validator(mode="after")
    def _exactly_one_geometry(self) -> Obstacle:
        if (self.position is None) == (self.polygon is None):
            raise ValueError(
                f"Препятствие {self.id}: нужна ровно одна геометрия — точка или полигон"
            )
        return self


class AltitudeCorridor(Model):
    """Коридор высот (заказчик, №10).

    Съёмка ведётся на одной высоте; на перелётах борта разводятся по эшелонам
    внутри коридора. Пример заказчика: съёмка 150 м, коридор до 700 м.
    """

    max_altitude_agl_m: float = Field(gt=0, description="Верхняя граница всех высот, м AGL")


class QualityRequirement(Model):
    """Требования к качеству — вход сенсорной модели (E2).

    Качество задаётся GSD (заказчик, №26). Перекрытия не обязательны: по
    умолчанию берутся из конфига типа съёмки `data/survey_presets.yaml` (№25).
    """

    gsd_m: float = Field(gt=0, description="Целевое разрешение на местности, метры на пиксель")
    forward_overlap: float | None = Field(
        default=None, ge=0.0, lt=1.0, description="Продольное перекрытие, доля 0..1"
    )
    side_overlap: float | None = Field(
        default=None, ge=0.0, lt=1.0, description="Поперечное перекрытие, доля 0..1"
    )


class FleetUnit(Model):
    """Строка состава флота: борт из каталога, сколько штук и с какой нагрузкой."""

    vehicle_id: str = Field(description="Идентификатор из data/vehicles.yaml")
    count: int = Field(default=1, ge=1)
    payload_id: str | None = Field(
        default=None, description="Нагрузка; None — выберет модель съёмки (E2)"
    )
    site_id: str | None = Field(
        default=None, description="Приписка к площадке; None — выберет распределение (E6)"
    )
    battery_sets: int | None = Field(
        default=None, ge=1, description="Предел числа вылетов на борт; None — не ограничено"
    )


class Scenario(Model):
    """Полное задание на планирование. Координаты — WGS84.

    Это ГРАНИЦА системы: дальше по конвейеру всё в метрах (инвариант 1).
    """

    id: str = Field(default="scenario")
    name: str = ""
    description: str = ""

    area: GeoMultiPolygon = Field(description="Область съёмки: полигон или мультиполигон")
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list)
    airspace_boundary: GeoPolygon | None = Field(
        default=None, description="Граница разрешённого воздушного пространства"
    )
    obstacles: list[Obstacle] = Field(default_factory=list, description="Высотные препятствия")
    altitude_corridor: AltitudeCorridor
    sites: list[Site] = Field(min_length=1, description="Площадки взлёта-посадки")

    survey_type: SurveyType = SurveyType.RGB
    quality: QualityRequirement
    wind: Wind = Field(default_factory=lambda: Wind(speed_mps=0.0, direction_deg=0.0))
    fleet: list[FleetUnit] = Field(min_length=1)
    vehicles: list[Vehicle] = Field(
        default_factory=list,
        description=(
            "ТТХ, заданные пользователем: запись заменяет модель каталога с тем же id "
            "или добавляет новую. Пусто — только каталог data/vehicles.yaml"
        ),
    )

    mode: OptimizationMode = OptimizationMode.MIN_MAKESPAN
    alpha: float = Field(
        default=assumptions.DEFAULT_ALPHA,
        ge=0.0,
        le=1.0,
        description="Вес срока в свёртке α·T + (1−α)·R — для фронта Парето",
    )
    start_time: datetime | None = Field(
        default=None, description="Начало работ, UTC. Нужно для окон временных зон"
    )
    deadline_s: float | None = Field(
        default=None, gt=0, description="Ограничение по сроку от начала работ, секунды"
    )
    turnaround_s: float | None = Field(
        default=None, ge=0, description="Межвылетная подготовка, секунды; None — допущение"
    )
    min_sun_elevation_deg: float | None = Field(
        default=None,
        ge=0.0,
        lt=90.0,
        description=(
            "Снимать только при высоте Солнца не ниже этой, градусы (норма — 25° на цветную, "
            "20° на черно-белую). None — время суток не учитывается. Нужно время начала работ"
        ),
    )
    seed: int = Field(
        default=assumptions.DEFAULT_SEED,
        description="Зерно ГПСЧ: один сценарий + один seed = один план",
    )

    @field_validator("sites")
    @classmethod
    def _unique_site_ids(cls, sites: list[Site]) -> list[Site]:
        ids = [site.id for site in sites]
        if len(set(ids)) != len(ids):
            raise ValueError("Идентификаторы площадок должны быть уникальны")
        return sites

    @field_validator("vehicles")
    @classmethod
    def _unique_vehicle_ids(cls, vehicles: list[Vehicle]) -> list[Vehicle]:
        ids = [vehicle.id for vehicle in vehicles]
        if len(set(ids)) != len(ids):
            raise ValueError("Модели с ТТХ пользователя повторяются: у каждой свой id")
        return vehicles

    @model_validator(mode="after")
    def _consistency(self) -> Scenario:
        known = {site.id for site in self.sites}
        for unit in self.fleet:
            if unit.site_id is not None and unit.site_id not in known:
                raise ValueError(
                    f"Борт {unit.vehicle_id} приписан к неизвестной площадке {unit.site_id!r}"
                )
        if not self.primary_sites():
            # Заказчик, №41. Номера вопросов в тексты для пользователя не выносятся:
            # docs/customer_qa.md — рабочий файл команды.
            raise ValueError(
                "Нужна хотя бы одна штатная площадка: резервные для работы не используются"
            )
        if self.min_sun_elevation_deg is not None and self.start_time is None:
            raise ValueError(
                "Ограничение по высоте Солнца требует время начала работ (start_time): "
                "без даты и времени окна съёмки не посчитать"
            )
        total = sum(unit.count for unit in self.fleet)
        if total > assumptions.MAX_FLEET_VEHICLES:
            # Заказчик, №2, №43.
            raise ValueError(
                f"Во флоте {total} бортов, допустимо не больше {assumptions.MAX_FLEET_VEHICLES}"
            )
        return self

    def primary_sites(self) -> list[Site]:
        """Штатные площадки взлёта-посадки."""
        return [site for site in self.sites if site.kind is SiteKind.PRIMARY]

    def reserve_sites(self) -> list[Site]:
        """Резервные площадки: внештатная посадка и зона ожидания."""
        return [site for site in self.sites if site.kind is SiteKind.RESERVE]


# --------------------------------------------------------------------------
# Импорт входных слоёв
# --------------------------------------------------------------------------


class LayerStats(Model):
    """Что импорт сделал с одним слоем (docs/input_formats.md §1)."""

    read: int = Field(default=0, ge=0, description="Объектов в файле")
    kept: int = Field(default=0, ge=0, description="Принято в сценарий")
    fixed: int = Field(default=0, ge=0, description="Исправлено починкой геометрии")
    dropped: int = Field(default=0, ge=0, description="Выброшено как непригодное")
    skipped: int = Field(default=0, ge=0, description="Отброшено отбором по области")


class ImportReport(Model):
    """Отчёт импорта: что прочитано, что починено, что выброшено.

    Отдаётся вместе со сценарием и показывается пользователю. Молча чинить
    или выбрасывать объекты входного файла нельзя: заказчик проверяет именно
    безопасность плана (№4), а она начинается с того, что мы прочитали.
    """

    area: LayerStats = Field(default_factory=LayerStats)
    zones: LayerStats = Field(default_factory=LayerStats)
    obstacles: LayerStats = Field(default_factory=LayerStats)
    area_km2: float = Field(default=0.0, ge=0.0, description="Площадь принятой области, км²")
    warnings: list[PlanWarning] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Стадия: SurveyPlan — параметры съёмки
# --------------------------------------------------------------------------


class SurveyParams(Model):
    """Параметры съёмки для одной пары «борт × нагрузка». Результат E2."""

    vehicle_id: str
    payload_id: str
    survey_type: SurveyType

    altitude_agl_m: float = Field(gt=0, description="Высота съёмки над поверхностью, метры")
    gsd_m: float = Field(gt=0, description="Фактическое разрешение на местности, м/пиксель")
    swath_width_m: float = Field(gt=0, description="Ширина полосы захвата, метры")
    strip_spacing_m: float = Field(gt=0, description="Шаг между галсами, метры")
    survey_speed_mps: float = Field(
        gt=0, description="Путевая скорость на галсе — её держит автопилот, м/с"
    )
    transit_speed_mps: float = Field(gt=0, description="Путевая скорость на перелёте, м/с")
    forward_overlap: float = Field(ge=0.0, lt=1.0)
    side_overlap: float = Field(ge=0.0, lt=1.0)
    shot_interval_s: float | None = Field(default=None, description="Интервал съёмки, секунды")


class SurveyPlan(Model):
    """Результат сенсорной модели по всему флоту сценария.

    `per_vehicle` может быть пустым: тогда задание невыполнимо, а причины —
    в `warnings` уровня ERROR.
    """

    per_vehicle: list[SurveyParams] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = Field(default=False, description="True — стадия работала на заглушке")

    def for_vehicle(self, vehicle_id: str) -> SurveyParams | None:
        """Параметры съёмки конкретного борта либо None, если борт исключён."""
        for params in self.per_vehicle:
            if params.vehicle_id == vehicle_id:
                return params
        return None


# --------------------------------------------------------------------------
# Стадия: Decomposition — подобласти
# --------------------------------------------------------------------------


class Cell(Model):
    """Подобласть — единица распределения работ между бортами (E3)."""

    id: str
    polygon: MetricPolygon
    area_m2: float = Field(ge=0)
    centroid: MetricPoint
    row: int = Field(ge=0, description="Ряд сетки декомпозиции")
    col: int = Field(ge=0, description="Столбец сетки декомпозиции")


class Decomposition(Model):
    """Рабочая маска, разрезанная на подобласти. Метры (инвариант 1)."""

    crs: CrsInfo
    cells: list[Cell] = Field(default_factory=list)
    azimuth_deg: float = Field(ge=0.0, lt=360.0, description="Направление галсов, градусы")
    cell_size_m: float = Field(gt=0, description="Сторона ячейки сетки, метры")
    work_area_m2: float = Field(ge=0, description="Площадь рабочей маски, м²")
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = False

    def cell(self, cell_id: str) -> Cell | None:
        """Подобласть по идентификатору."""
        for item in self.cells:
            if item.id == cell_id:
                return item
        return None


# --------------------------------------------------------------------------
# Стадия: Coverage — галсы подобластей
# --------------------------------------------------------------------------


class Strip(Model):
    """Один галс: геометрия в метрах, высота, путевая скорость."""

    id: str
    cell_id: str
    vehicle_id: str
    line: MetricLineString
    length_m: float = Field(ge=0)
    altitude_agl_m: float = Field(gt=0)
    speed_mps: float = Field(gt=0, description="Путевая скорость на галсе, м/с")
    azimuth_deg: float = Field(ge=0.0, lt=360.0, description="Направление съёмки, градусы")

    def start(self) -> MetricPoint:
        """Первая точка галса."""
        return self.line.points[0]

    def end(self) -> MetricPoint:
        """Последняя точка галса."""
        return self.line.points[-1]


class CellCoverage(Model):
    """Покрытие одной подобласти одним бортом."""

    cell_id: str
    vehicle_id: str
    strips: list[Strip] = Field(default_factory=list)
    maneuver_zones: list[MetricPolygon] = Field(
        default_factory=list,
        description="Защитные зоны манёвра на концах галсов, самолётный тип (заказчик, №38)",
    )
    covered_area_m2: float = Field(ge=0)
    serviceable: bool = Field(description="Может ли борт снять подобласть без нарушений")
    problem: str | None = Field(default=None, description="Почему не может, если не может")


class CoverageSet(Model):
    """Покрытие подобластей, распределённых по бортам. Геометрия — метры."""

    crs: CrsInfo
    cells: list[CellCoverage] = Field(default_factory=list)
    work_area_m2: float = Field(ge=0, description="Площадь рабочей маски, м²")
    covered_area_m2: float = Field(ge=0, description="Площадь, накрытая полосами захвата, м²")
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = False

    def strips(self) -> list[Strip]:
        """Все галсы плана."""
        return [strip for cell in self.cells for strip in cell.strips]


# --------------------------------------------------------------------------
# Стадия: Allocation — подобласти по вылетам
# --------------------------------------------------------------------------


class SortieAssignment(Model):
    """Назначение: борт, площадки взлёта и посадки, подобласти и их порядок."""

    sortie_id: str
    vehicle_id: str
    vehicle_instance: int = Field(
        default=0, ge=0, description="Номер экземпляра борта внутри строки флота"
    )
    takeoff_site_id: str
    landing_site_id: str = Field(description="Может отличаться от площадки взлёта (№29)")
    cell_ids: list[str] = Field(default_factory=list)
    trip_index: int = Field(default=0, ge=0, description="Номер вылета этого борта, с нуля")


class Allocation(Model):
    """Распределение подобластей по вылетам — до расчёта расписания."""

    crs: CrsInfo
    assignments: list[SortieAssignment] = Field(default_factory=list)
    unassigned_cell_ids: list[str] = Field(
        default_factory=list, description="Подобласти, которые не взял ни один вылет"
    )
    algorithm: str = Field(description="Имя алгоритма: baseline / constructive / local_search")
    mode: OptimizationMode
    alpha: float = Field(ge=0.0, le=1.0)
    seed: int
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = False


# --------------------------------------------------------------------------
# Стадия: вылеты и расписание
# --------------------------------------------------------------------------


class Leg(Model):
    """Участок полёта одной фазы.

    Инвариант 7: вылет — сумма фаз, у каждой своя скорость. На галсе время
    равно длине, делённой на путевую скорость, которую держит автопилот.
    """

    phase: FlightPhase
    waypoints: list[Waypoint] = Field(min_length=2)
    length_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    ground_speed_mps: float = Field(ge=0, description="Средняя путевая скорость, м/с")
    strip_id: str | None = Field(default=None, description="Заполняется для фазы STRIP")
    cell_id: str | None = Field(default=None, description="Подобласть, над которой идёт участок")


class Sortie(Model):
    """Один вылет одного борта: от взлёта до посадки."""

    id: str
    vehicle_id: str
    vehicle_instance: int = 0
    takeoff_site_id: str
    landing_site_id: str
    trip_index: int = 0

    legs: list[Leg] = Field(default_factory=list)
    start_time_s: float = Field(ge=0, description="Момент взлёта от начала работ, секунды")
    duration_s: float = Field(ge=0, description="Полётное время вылета, секунды")
    distance_m: float = Field(ge=0)
    energy_fraction_used: float = Field(
        ge=0, description="Доля автономности, израсходованная вылетом (линейная модель, №35)"
    )
    survey_altitude_m: float = Field(gt=0, description="Высота съёмки, м AGL — одна на вылет")
    transit_altitude_m: float = Field(gt=0, description="Эшелон перелёта, м AGL")
    cell_ids: list[str] = Field(default_factory=list)
    strip_ids: list[str] = Field(default_factory=list)

    def end_time_s(self) -> float:
        """Момент посадки от начала работ, секунды."""
        return self.start_time_s + self.duration_s


class ScheduleEntry(Model):
    """Строка расписания (заказчик, №31)."""

    sortie_id: str
    vehicle_id: str
    vehicle_instance: int
    trip_index: int
    takeoff_site_id: str
    landing_site_id: str
    start_s: float = Field(ge=0, description="Взлёт от начала работ, секунды")
    end_s: float = Field(ge=0, description="Посадка от начала работ, секунды")
    delay_s: float = Field(ge=0, description="Сдвиг старта ради разведения бортов, секунды")
    transit_altitude_m: float = Field(gt=0)


class Schedule(Model):
    """Расписание вылетов на общей оси времени (E14)."""

    start_time: datetime | None = Field(default=None, description="Начало работ, UTC")
    entries: list[ScheduleEntry] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = False


class Recommendation(Model):
    """Что добавить, чтобы задание стало выполнимым (заказчик, №33, №42)."""

    kind: RecommendationKind
    message: str
    position: MetricPoint | None = Field(
        default=None, description="Предлагаемое место (например, площадки), метры"
    )
    vehicle_id: str | None = None


class MissionMetrics(Model):
    """Метрики плана. Ровно то, что оптимизируется и сравнивается с baseline."""

    makespan_s: float = Field(ge=0, description="T — срок: до посадки последнего борта, секунды")
    total_flight_time_s: float = Field(ge=0, description="Суммарный налёт всех бортов, секунды")
    cycle_count: int = Field(ge=0, description="Число циклов взлёт-посадка")
    resource_s: float = Field(ge=0, description="R — ресурс: налёт + τ_цикл × циклы, секунды")
    total_distance_m: float = Field(ge=0)
    sortie_count: int = Field(ge=0)
    vehicle_count: int = Field(ge=0)
    cells_planned: int = Field(ge=0)
    cells_covered: int = Field(ge=0)
    objective_value: float = Field(description="Значение критерия выбранного режима, секунды")
    weighted_value: float = Field(description="α·T + (1−α)·R, секунды — для фронта Парето")
    mode: OptimizationMode
    alpha: float = Field(ge=0.0, le=1.0)
    deadline_s: float | None = None
    deadline_met: bool | None = None


class Mission(Model):
    """Итог конвейера: полный план или отчёт о невыполнимости.

    Инвариант 10: частичных планов нет. У невыполнимого плана `sorties` пуст,
    а `reasons` и `recommendations` объясняют, чего не хватает.
    """

    id: str
    scenario_id: str
    crs: CrsInfo
    origin: GeoPoint = Field(description="Точка привязки проекции: центр области, WGS84")
    status: MissionStatus

    sorties: list[Sortie] = Field(default_factory=list)
    schedule: Schedule | None = None
    metrics: MissionMetrics | None = None
    reasons: list[PlanWarning] = Field(
        default_factory=list, description="Причины невыполнимости — нарушенные жёсткие правила"
    )
    recommendations: list[Recommendation] = Field(default_factory=list)
    warnings: list[PlanWarning] = Field(default_factory=list)

    algorithm: str
    mode: OptimizationMode
    alpha: float = Field(ge=0.0, le=1.0)
    seed: int
    altitude_mode: AltitudeMode = AltitudeMode.RELATIVE_TO_GROUND
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    stub_stages: list[Stage] = Field(
        default_factory=list,
        description="Стадии, отработавшие на заглушке. Непустой список = план не окончательный",
    )

    def is_feasible(self) -> bool:
        """True — полный план без нарушений жёстких правил."""
        return self.status is MissionStatus.FEASIBLE

    def is_stub(self) -> bool:
        """True, если хотя бы одна стадия конвейера работала на заглушке."""
        return bool(self.stub_stages)


# --------------------------------------------------------------------------
# Полный результат прогона: нужен API для инспектора стадий (E9/E11)
# --------------------------------------------------------------------------


class PipelineResult(Model):
    """Все промежуточные стадии одного прогона.

    Стадии после той, на которой задание оказалось невыполнимым, отсутствуют
    (None): конвейер останавливается там, где дальше считать не из чего.
    """

    scenario_id: str
    crs: CrsInfo
    survey_plan: SurveyPlan
    decomposition: Decomposition | None = None
    allocation: Allocation | None = None
    coverage: CoverageSet | None = None
    mission: Mission
    timings_s: dict[str, float] = Field(
        default_factory=dict, description="Длительность каждой стадии, секунды"
    )

    def warnings(self) -> list[PlanWarning]:
        """Все предупреждения конвейера, в порядке стадий."""
        items: list[PlanWarning] = [*self.survey_plan.warnings]
        for stage in (self.decomposition, self.allocation, self.coverage):
            if stage is not None:
                items.extend(stage.warnings)
        if self.mission.schedule is not None:
            items.extend(self.mission.schedule.warnings)
        items.extend(self.mission.warnings)
        return items


# --------------------------------------------------------------------------
# Подсказки по пулу техники (заказчик, №51): поверх конвейера, E6
# --------------------------------------------------------------------------


class HintKind(str, Enum):
    """Вопрос подсказки (заказчик, №51)."""

    BOARDS = "boards"  # сколько времени займёт работа при N бортах
    DEADLINE = "deadline"  # сколько бортов нужно, чтобы успеть к сроку
    SWEEP = "sweep"  # срок при каждом N — от одного борта до всего пула


class HintBoard(Model):
    """Борт пула — в том порядке, в каком подсказка привлекает борта."""

    vehicle_id: str
    vehicle_name: str
    productivity_m2ps: float = Field(
        gt=0, description="Шаг галсов × путевая скорость на галсе, м²/с"
    )
    site_id: str | None = Field(default=None, description="Приписка к площадке из сценария")


class HintRun(Model):
    """Один прогон конвейера на N бортах пула."""

    boards: int = Field(ge=1, description="Число бортов N")
    fleet: list[FleetUnit] = Field(description="Состав флота этого прогона")
    status: MissionStatus
    makespan_s: float | None = Field(
        default=None, ge=0, description="Срок, секунды; None — план невыполним"
    )
    resource_s: float | None = Field(default=None, ge=0, description="Ресурс, секунды")
    sortie_count: int | None = Field(default=None, ge=0)
    meets_deadline: bool | None = Field(
        default=None, description="Успевает к сроку; None — срок не задан или план невыполним"
    )
    reasons: list[PlanWarning] = Field(
        default_factory=list, description="Причины невыполнимости, по одной на код"
    )
    note: str = Field(default="", description="Как подобран состав, если не по порядку пула")
    elapsed_s: float = Field(ge=0, description="Время расчёта, секунды")


class HintReport(Model):
    """Ответ-подсказка: вопрос, прогоны конвейера и ответ одной фразой."""

    scenario_id: str
    kind: HintKind
    boards: int | None = Field(default=None, ge=1, description="Вопрос: число бортов")
    deadline_s: float | None = Field(
        default=None, gt=0, description="Срок от начала работ, секунды: из вопроса или сценария"
    )
    pool: list[HintBoard] = Field(
        default_factory=list, description="Пул — флот сценария — в порядке привлечения"
    )
    runs: list[HintRun] = Field(default_factory=list, description="Прогоны по возрастанию N")
    answer_boards: int | None = Field(
        default=None, description="Число бортов в ответе; None — ответа нет"
    )
    answer: str = Field(description="Ответ одной фразой — для человека")
    warnings: list[PlanWarning] = Field(default_factory=list)


__all__ = [
    "AltitudeCorridor",
    "AltitudeMode",
    "Allocation",
    "Cell",
    "CellCoverage",
    "CoverageSet",
    "CrsInfo",
    "Decomposition",
    "ExportFormat",
    "FleetUnit",
    "FlightPhase",
    "GeoMultiPolygon",
    "GeoPoint",
    "GeoPolygon",
    "HintBoard",
    "HintKind",
    "HintReport",
    "HintRun",
    "ImportReport",
    "LayerStats",
    "Leg",
    "MetricLineString",
    "MetricPoint",
    "MetricPolygon",
    "Mission",
    "MissionMetrics",
    "MissionStatus",
    "Model",
    "NoFlyZone",
    "Obstacle",
    "OptimizationMode",
    "Payload",
    "PayloadKind",
    "PipelineResult",
    "PlanWarning",
    "QualityRequirement",
    "Recommendation",
    "RecommendationKind",
    "Scenario",
    "Schedule",
    "ScheduleEntry",
    "Severity",
    "Site",
    "SiteKind",
    "Sortie",
    "SortieAssignment",
    "Stage",
    "Strip",
    "SurveyParams",
    "SurveyPlan",
    "SurveyType",
    "Vehicle",
    "VehicleType",
    "Waypoint",
    "Wind",
]
