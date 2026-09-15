"""Общий контракт данных: pydantic-модели всех стадий конвейера.

ЭТО ЕДИНСТВЕННАЯ ТОЧКА СИНХРОНИЗАЦИИ между `core`, `api` и `web`
(инвариант 3, PROJECT_BRIEF.md §5). Правка ломает три параллельных потока
работы сразу, поэтому порядок изменения зафиксирован в AGENT.md §4:
аддитивно → перегенерировать TS-типы → отметить в docs/current_state.md.

Две системы координат и почему их две
-------------------------------------
Инвариант 1: вся геометрия считается в МЕТРИЧЕСКОЙ проекции, WGS84 живёт
только на границах системы. Чтобы это нельзя было нарушить незаметно,
географические и метрические координаты — РАЗНЫЕ ТИПЫ:

* `GeoPoint` / `GeoPolygon`   — WGS84, градусы. Только `Scenario` (вход) и
  экспорт (выход). Внутри конвейера их быть не должно.
* `MetricPoint` / `MetricPolygon` / `MetricLineString` / `Waypoint` — метры
  в рабочей проекции `CrsInfo`. Всё, что между стадиями.

Подставить одно вместо другого не получится: pydantic отвергнет по именам
полей (`lon`/`lat` против `x_m`/`y_m`).

Единицы измерения — в именах полей
----------------------------------
Суффикс поля называет единицу: `_m` метры, `_s` секунды, `_mps` м/с,
`_deg` градусы, `_wh` ватт-часы, `_m2` квадратные метры. Доли (0..1) —
суффикс `_fraction` или имя `overlap`. Поле без суффикса — не число
(идентификатор, перечисление, геометрия).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------
# Перечисления
# --------------------------------------------------------------------------


class VehicleType(str, Enum):
    """Тип БВС. Определяет кинематику разворота и схему взлёта/посадки."""

    FIXED_WING = "fixed_wing"
    MULTIROTOR = "multirotor"


class SurveyType(str, Enum):
    """Тип съёмки. У каждого — свой пресет сенсорной модели (E2)."""

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
    """Фаза полёта. Инвариант 7: время считается по фазам, а не «длина / скорость»."""

    TAKEOFF = "takeoff"
    CLIMB = "climb"
    TRANSIT = "transit"
    STRIP = "strip"
    TURN = "turn"
    DESCENT = "descent"
    LANDING = "landing"


class SiteKind(str, Enum):
    """Назначение наземной площадки."""

    PRIMARY = "primary"  # штатный взлёт и посадка
    RESERVE = "reserve"  # запасная площадка, проверяется валидатором (E7)


class Severity(str, Enum):
    """Тяжесть предупреждения валидатора."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Stage(str, Enum):
    """Стадия конвейера. Нужна, чтобы предупреждение было адресным."""

    GEO = "geo"
    SURVEY = "survey"
    COVERAGE = "coverage"
    FLIGHT = "flight"
    TRANSIT = "transit"
    ALLOCATE = "allocate"
    VALIDATE = "validate"
    EXPORT = "export"


class ExportFormat(str, Enum):
    """Формат выгрузки полётного задания."""

    GEOJSON = "geojson"
    KML = "kml"


class AltitudeMode(str, Enum):
    """Режим высоты в экспорте.

    Критично для KML: три режима дают три РАЗНЫХ смысла одной и той же цифры
    (PROJECT_BRIEF.md §7). Проект работает без ЦМР, поэтому по умолчанию
    высота относительна точке старта.
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
    alt_m: float | None = Field(default=None, description="Высота, метры (см. AltitudeMode)")

    @model_validator(mode="before")
    @classmethod
    def _accept_sequence(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            if len(value) == 2:
                return {"lon": value[0], "lat": value[1]}
            if len(value) == 3:
                return {"lon": value[0], "lat": value[1], "alt_m": value[2]}
            raise ValueError("Точка WGS84 записывается как [lon, lat] или [lon, lat, alt_m]")
        return value


class GeoPolygon(Model):
    """Полигон в WGS84: внешний контур и произвольное число отверстий.

    Кольца не требуется замыкать — замыкание доопределяется при переводе в
    shapely. Принимает компактную запись в виде координат GeoJSON Polygon:
    `[[[lon, lat], ...], [[lon, lat], ...]]` (первое кольцо — внешнее).
    """

    outer: list[GeoPoint] = Field(min_length=3, description="Внешний контур, WGS84")
    holes: list[list[GeoPoint]] = Field(default_factory=list, description="Отверстия, WGS84")

    @model_validator(mode="before")
    @classmethod
    def _accept_rings(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            rings = list(value)
            if not rings:
                raise ValueError("Полигон должен содержать хотя бы внешнее кольцо")
            return {"outer": rings[0], "holes": rings[1:]}
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
    """Точка маршрута: плановое положение, высота и путевая скорость.

    Высота — над точкой взлёта (рельеф не моделируется, см.
    `core.assumptions.TERRAIN_MODEL`). Именно это значение уходит в KML
    с режимом `relativeToGround`.
    """

    x_m: float = Field(description="Координата X рабочей проекции, метры")
    y_m: float = Field(description="Координата Y рабочей проекции, метры")
    alt_m: float = Field(description="Высота над точкой взлёта, метры")
    ground_speed_mps: float | None = Field(
        default=None, description="Путевая скорость на подходе к точке, м/с"
    )


# --------------------------------------------------------------------------
# Предупреждения
# --------------------------------------------------------------------------


class PlanWarning(Model):
    """Предупреждение о плане.

    AGENT.md §8: невыполнимый сценарий — это предупреждение или типизированное
    исключение с объяснением, а не тихий пустой результат. Заглушки стадий
    обязаны сообщать о себе именно здесь, иначе они «молча выглядят как
    настоящий расчёт».
    """

    code: str = Field(description="Машинный код, например 'stub.flight'")
    severity: Severity = Severity.WARNING
    stage: Stage
    message: str = Field(description="Человекочитаемое объяснение")
    context: dict[str, str] = Field(
        default_factory=dict, description="Идентификаторы: sortie_id, vehicle_id, strip_id и т.п."
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
    нет в официальном документе, остаются `None` и попадают в `gaps`.
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
    min_shot_interval_s: float | None = Field(
        default=None, description="Минимальный интервал между кадрами, секунды"
    )
    mass_kg: float | None = None
    gaps: list[str] = Field(
        default_factory=list, description="Известные пробелы ТТХ, дословно из каталога"
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
    endurance_s: float = Field(gt=0, description="Максимальная продолжительность полёта, секунды")
    max_wind_mps: float = Field(gt=0, description="Допустимая скорость ветра, м/с")
    turn_radius_m: float = Field(
        ge=0, description="Минимальный радиус разворота, метры (0 — мультиротор)"
    )

    min_altitude_agl_m: float | None = Field(default=None, description="Мин. безопасная высота, м")
    max_altitude_agl_m: float | None = Field(default=None, description="Потолок над землёй, м")
    max_range_m: float | None = Field(default=None, description="Протяжённость маршрута, метры")

    energy_wh: float | None = Field(default=None, description="Энергия АКБ, Вт·ч")
    battery_reserve_fraction: float = Field(
        ge=0.0, lt=1.0, description="Неснижаемый резерв заряда, доля 0..1"
    )
    battery_swap_time_s: float = Field(ge=0, description="Время замены АКБ между вылетами, секунды")

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

    def payload(self, payload_id: str) -> Payload | None:
        """Нагрузка по идентификатору, либо None."""
        for item in self.payloads:
            if item.id == payload_id:
                return item
        return None


# --------------------------------------------------------------------------
# Вход конвейера: Scenario (единственное место, где живёт WGS84 на входе)
# --------------------------------------------------------------------------


class Wind(Model):
    """Ветер.

    ИНВАРИАНТ 4. `direction_deg` — МЕТЕОРОЛОГИЧЕСКАЯ конвенция: направление,
    ОТКУДА дует. 0° — северный ветер (дует с севера на юг), 90° — восточный.
    Перепутанный знак даёт зеркальный план и обнаруживается только на демо.
    Перевод в вектор сноса — `core.flight.wind.wind_vector`, и только там.

    Допущение: ветер постоянен по всей области и по высоте (профиля нет).
    """

    speed_mps: float = Field(ge=0.0, description="Скорость ветра, м/с")
    direction_deg: float = Field(
        ge=0.0, le=360.0, description="Откуда дует, градусы от севера по часовой стрелке"
    )


class Site(Model):
    """Наземная площадка взлёта-посадки, WGS84."""

    id: str
    name: str = ""
    position: GeoPoint
    kind: SiteKind = SiteKind.PRIMARY


class NoFlyZone(Model):
    """Запретная зона, WGS84.

    Высотные границы опциональны: зона без них считается запретной на всех
    высотах, что консервативнее и безопаснее.
    """

    id: str
    name: str = ""
    polygon: GeoPolygon
    min_alt_m: float | None = None
    max_alt_m: float | None = None


class QualityRequirement(Model):
    """Требования к качеству материала — вход сенсорной модели (E2)."""

    gsd_m: float = Field(gt=0, description="Целевое разрешение на местности, метры на пиксель")
    forward_overlap: float = Field(ge=0.0, lt=1.0, description="Продольное перекрытие, доля 0..1")
    side_overlap: float = Field(ge=0.0, lt=1.0, description="Поперечное перекрытие, доля 0..1")


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
    battery_sets: int = Field(
        default=1, ge=1, description="Комплектов АКБ на борт — предел числа вылетов"
    )


class Scenario(Model):
    """Полное задание на планирование. Координаты — WGS84.

    Это ГРАНИЦА системы: дальше по конвейеру всё в метрах (инвариант 1).
    """

    id: str = Field(default="scenario")
    name: str = ""
    description: str = ""

    area: GeoPolygon = Field(description="Область съёмки")
    no_fly_zones: list[NoFlyZone] = Field(default_factory=list)
    airspace_boundary: GeoPolygon | None = Field(
        default=None, description="Граница разрешённого воздушного пространства"
    )
    sites: list[Site] = Field(min_length=1, description="Площадки взлёта-посадки")

    survey_type: SurveyType = SurveyType.RGB
    quality: QualityRequirement
    wind: Wind = Field(default_factory=lambda: Wind(speed_mps=0.0, direction_deg=0.0))
    fleet: list[FleetUnit] = Field(min_length=1)

    alpha: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Вес makespan в целевой функции α·makespan + (1−α)·суммарный налёт",
    )
    seed: int = Field(default=42, description="Зерно ГПСЧ: один сценарий + один seed = один план")

    @field_validator("sites")
    @classmethod
    def _unique_site_ids(cls, sites: list[Site]) -> list[Site]:
        ids = [site.id for site in sites]
        if len(set(ids)) != len(ids):
            raise ValueError("Идентификаторы площадок должны быть уникальны")
        return sites

    @model_validator(mode="after")
    def _fleet_refers_to_known_sites(self) -> Scenario:
        known = {site.id for site in self.sites}
        for unit in self.fleet:
            if unit.site_id is not None and unit.site_id not in known:
                raise ValueError(
                    f"Борт {unit.vehicle_id} приписан к неизвестной площадке {unit.site_id!r}"
                )
        return self

    def primary_sites(self) -> list[Site]:
        """Штатные площадки взлёта-посадки (без резервных)."""
        return [site for site in self.sites if site.kind is SiteKind.PRIMARY]


# --------------------------------------------------------------------------
# Стадия 1: SurveyPlan — параметры съёмки
# --------------------------------------------------------------------------


class SurveyParams(Model):
    """Параметры съёмки для одной пары «борт × нагрузка». Результат E2."""

    vehicle_id: str
    payload_id: str
    survey_type: SurveyType

    altitude_agl_m: float = Field(gt=0, description="Высота съёмки над точкой старта, метры")
    gsd_m: float = Field(gt=0, description="Фактическое разрешение на местности, м/пиксель")
    swath_width_m: float = Field(gt=0, description="Ширина полосы захвата, метры")
    strip_spacing_m: float = Field(gt=0, description="Шаг между галсами, метры")
    survey_speed_mps: float = Field(gt=0, description="Скорость на галсе, м/с")
    forward_overlap: float = Field(ge=0.0, lt=1.0)
    side_overlap: float = Field(ge=0.0, lt=1.0)
    shot_interval_s: float | None = Field(default=None, description="Интервал съёмки, секунды")


class SurveyPlan(Model):
    """Результат сенсорной модели по всему флоту сценария."""

    per_vehicle: list[SurveyParams] = Field(min_length=1)
    reference_vehicle_id: str = Field(
        description="Борт, по параметрам которого строится покрытие (см. TODO(E3) в coverage)"
    )
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = Field(default=False, description="True — стадия работала на заглушке")

    def for_vehicle(self, vehicle_id: str) -> SurveyParams:
        """Параметры съёмки конкретного борта; при отсутствии — опорные."""
        for params in self.per_vehicle:
            if params.vehicle_id == vehicle_id:
                return params
        return self.reference()

    def reference(self) -> SurveyParams:
        """Опорный набор параметров, по которому построены галсы."""
        for params in self.per_vehicle:
            if params.vehicle_id == self.reference_vehicle_id:
                return params
        return self.per_vehicle[0]


# --------------------------------------------------------------------------
# Стадия 2: CoverageSet — галсы
# --------------------------------------------------------------------------


class Strip(Model):
    """Один галс: геометрия в метрах, высота, скорость."""

    id: str
    line: MetricLineString
    length_m: float = Field(ge=0)
    altitude_agl_m: float = Field(gt=0)
    speed_mps: float = Field(gt=0)
    azimuth_deg: float = Field(ge=0.0, lt=360.0, description="Направление съёмки, градусы")

    def start(self) -> MetricPoint:
        """Первая точка галса."""
        return self.line.points[0]

    def end(self) -> MetricPoint:
        """Последняя точка галса."""
        return self.line.points[-1]


class CoverageSet(Model):
    """Покрытие области галсами. Геометрия — метры (инвариант 1)."""

    crs: CrsInfo
    strips: list[Strip] = Field(default_factory=list)
    azimuth_deg: float = Field(ge=0.0, lt=360.0, description="Выбранное направление галсов")
    work_area_m2: float = Field(ge=0, description="Площадь рабочей маски, м²")
    covered_area_m2: float = Field(ge=0, description="Площадь, покрытая полосами захвата, м²")
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = False

    def strip(self, strip_id: str) -> Strip | None:
        """Галс по идентификатору."""
        for item in self.strips:
            if item.id == strip_id:
                return item
        return None


# --------------------------------------------------------------------------
# Стадия 3: Allocation — распределение галсов по вылетам
# --------------------------------------------------------------------------


class SortieAssignment(Model):
    """Назначение: какой борт, с какой площадки, какие галсы и в каком порядке."""

    sortie_id: str
    vehicle_id: str
    vehicle_instance: int = Field(
        default=0, ge=0, description="Номер экземпляра борта внутри строки флота"
    )
    site_id: str
    strip_ids: list[str] = Field(default_factory=list)
    trip_index: int = Field(default=0, ge=0, description="Номер вылета этого борта, с нуля")


class Allocation(Model):
    """Распределение по флоту — до расчёта стоимости."""

    crs: CrsInfo
    assignments: list[SortieAssignment] = Field(default_factory=list)
    algorithm: str = Field(description="Имя алгоритма: baseline / constructive / local_search")
    alpha: float = Field(ge=0.0, le=1.0)
    seed: int
    warnings: list[PlanWarning] = Field(default_factory=list)
    stub: bool = False


# --------------------------------------------------------------------------
# Стадия 4: Mission — готовый план
# --------------------------------------------------------------------------


class Leg(Model):
    """Участок полёта одной фазы.

    Инвариант 7: `duration_s` НЕ равно `length_m / speed`. Учитываются фаза,
    ветер, развороты. Как именно — определяет модель полёта (E4).
    """

    phase: FlightPhase
    waypoints: list[Waypoint] = Field(min_length=2)
    length_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    ground_speed_mps: float = Field(ge=0, description="Средняя путевая скорость, м/с")
    energy_wh: float | None = Field(default=None, description="Израсходованная энергия, Вт·ч")
    strip_id: str | None = Field(default=None, description="Заполняется для фазы STRIP")


class Sortie(Model):
    """Один вылет одного борта: от взлёта до посадки."""

    id: str
    vehicle_id: str
    vehicle_instance: int = 0
    site_id: str
    trip_index: int = 0

    legs: list[Leg] = Field(default_factory=list)
    start_time_s: float = Field(ge=0, description="Момент взлёта от начала работ, секунды")
    duration_s: float = Field(ge=0, description="Полётное время вылета, секунды")
    distance_m: float = Field(ge=0)
    energy_wh: float | None = None
    energy_fraction_used: float | None = Field(
        default=None, description="Доля израсходованной ёмкости АКБ, 0..1"
    )
    strip_ids: list[str] = Field(default_factory=list)

    def end_time_s(self) -> float:
        """Момент посадки от начала работ, секунды."""
        return self.start_time_s + self.duration_s


class MissionMetrics(Model):
    """Метрики плана. Ровно то, что оптимизируется и что сравнивается с baseline."""

    makespan_s: float = Field(ge=0, description="Время выполнения всех работ, секунды")
    total_flight_time_s: float = Field(ge=0, description="Суммарный налёт всех бортов, секунды")
    total_distance_m: float = Field(ge=0)
    sortie_count: int = Field(ge=0)
    vehicle_count: int = Field(ge=0)
    strips_planned: int = Field(ge=0)
    strips_covered: int = Field(ge=0)
    objective_value: float = Field(
        description=(
            "α·makespan + (1−α)·суммарный налёт, секунды. Функция зафиксирована (AGENT.md §7)"
        )
    )
    alpha: float = Field(ge=0.0, le=1.0)


class Mission(Model):
    """Готовое полётное задание группы БВС — выход конвейера."""

    id: str
    scenario_id: str
    crs: CrsInfo
    origin: GeoPoint = Field(description="Точка привязки проекции: центр области, WGS84")

    sorties: list[Sortie] = Field(default_factory=list)
    metrics: MissionMetrics
    warnings: list[PlanWarning] = Field(default_factory=list)

    algorithm: str
    alpha: float = Field(ge=0.0, le=1.0)
    seed: int
    altitude_mode: AltitudeMode = AltitudeMode.RELATIVE_TO_GROUND
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    stub_stages: list[Stage] = Field(
        default_factory=list,
        description="Стадии, отработавшие на заглушке. Непустой список = план не окончательный",
    )

    def is_stub(self) -> bool:
        """True, если хотя бы одна стадия конвейера работала на заглушке."""
        return bool(self.stub_stages)


# --------------------------------------------------------------------------
# Полный результат прогона: нужен API для инспектора стадий (E9/E11)
# --------------------------------------------------------------------------


class PipelineResult(Model):
    """Все промежуточные стадии одного прогона.

    Конвейер отлаживается по стадиям, и любую из них надо уметь показать на
    карте во время защиты (PROJECT_BRIEF.md §2).
    """

    scenario_id: str
    crs: CrsInfo
    survey_plan: SurveyPlan
    coverage: CoverageSet
    allocation: Allocation
    mission: Mission
    timings_s: dict[str, float] = Field(
        default_factory=dict, description="Длительность каждой стадии, секунды"
    )

    def warnings(self) -> list[PlanWarning]:
        """Все предупреждения конвейера, в порядке стадий."""
        return [
            *self.survey_plan.warnings,
            *self.coverage.warnings,
            *self.allocation.warnings,
            *self.mission.warnings,
        ]


__all__ = [
    "AltitudeMode",
    "Allocation",
    "CoverageSet",
    "CrsInfo",
    "ExportFormat",
    "FleetUnit",
    "FlightPhase",
    "GeoPoint",
    "GeoPolygon",
    "Leg",
    "MetricLineString",
    "MetricPoint",
    "MetricPolygon",
    "Mission",
    "MissionMetrics",
    "Model",
    "NoFlyZone",
    "Payload",
    "PayloadKind",
    "PipelineResult",
    "PlanWarning",
    "QualityRequirement",
    "Scenario",
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
