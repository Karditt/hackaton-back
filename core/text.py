"""Тексты для человека: числа, согласование, подписи значений контракта.

Сообщения плана (предупреждения, причины, советы) читает пользователь в вебе
и в выгрузке. Внутренние коды — `min_makespan`, `rgb`, `cruise_speed_mps`,
`geoscan-gemini_2-t3` — ему ничего не говорят, поэтому сообщения собираются из
подписей этого модуля. Числа — с десятичной запятой, как принято в русском
тексте.

Модуль чистый: ни расчётов, ни чтения файлов.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.schemas import FlightPhase, OptimizationMode, SiteKind, Sortie, SurveyType

#: Режимы оптимизации словами — «по критерию „минимум срока“».
MODE_LABELS: dict[OptimizationMode, str] = {
    OptimizationMode.MIN_MAKESPAN: "минимум срока",
    OptimizationMode.MIN_RESOURCE: "минимум ресурса",
}

#: Типы съёмки в предложном падеже — «камера для фотосъёмки (RGB)».
SURVEY_TYPE_LABELS: dict[SurveyType, str] = {
    SurveyType.RGB: "фотосъёмки (RGB)",
    SurveyType.MULTISPECTRAL: "мультиспектральной съёмки",
    SurveyType.IR: "инфракрасной съёмки",
    SurveyType.LIDAR: "лазерного сканирования",
    SurveyType.GEOPHYSICAL: "геофизической съёмки",
}

#: Фазы полёта — как в легенде карты.
PHASE_LABELS: dict[FlightPhase, str] = {
    FlightPhase.TAKEOFF: "взлёт",
    FlightPhase.CLIMB: "набор высоты",
    FlightPhase.TRANSIT: "перелёт",
    FlightPhase.STRIP: "галс съёмки",
    FlightPhase.TURN: "разворот",
    FlightPhase.HOLD: "ожидание в воздухе",
    FlightPhase.DESCENT: "снижение",
    FlightPhase.LANDING: "посадка",
}

#: Назначение площадки.
SITE_KIND_LABELS: dict[SiteKind, str] = {
    SiteKind.PRIMARY: "основная",
    SiteKind.RESERVE: "резервная",
}

#: Поля ТТХ модели словами — для пробелов каталога и предупреждений.
FIELD_LABELS: dict[str, str] = {
    "name": "название",
    "type": "тип",
    "cruise_speed_mps": "крейсерская скорость",
    "max_speed_mps": "максимальная скорость",
    "min_speed_mps": "минимальная скорость",
    "endurance_s": "продолжительность полёта",
    "max_wind_mps": "допустимый ветер",
    "turn_radius_m": "радиус разворота",
    "min_altitude_agl_m": "минимальная безопасная высота",
    "max_altitude_agl_m": "потолок над землёй",
    "max_range_m": "протяжённость маршрута",
    "energy_wh": "энергия АКБ",
    "battery": "АКБ",
    "battery_reserve_fraction": "отсечка заряда",
    "payloads": "полезная нагрузка",
    "max_payload_mass_kg": "масса полезной нагрузки",
    "survey_area_per_sortie": "площадь съёмки за вылет",
    "focal_length_mm": "фокусное расстояние",
    "sensor_width_mm": "ширина матрицы",
    "sensor_height_mm": "высота матрицы",
    "image_width_px": "ширина кадра",
    "image_height_px": "высота кадра",
}


def num(value: float, digits: int = 1) -> str:
    """Число с десятичной запятой: `num(2.834, 2)` → «2,83».

    Args:
        value: число.
        digits: знаков после запятой.
    """
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


def plural(count: int, forms: tuple[str, str, str]) -> str:
    """Форма слова после числа: 1 подобласть, 3 подобласти, 5 подобластей.

    Args:
        count: число.
        forms: формы для 1, 2–4 и 5–20.
    """
    tail = abs(count) % 100
    unit = tail % 10
    if 11 <= tail <= 14:
        return forms[2]
    if unit == 1:
        return forms[0]
    if 2 <= unit <= 4:
        return forms[1]
    return forms[2]


def counted(count: int, forms: tuple[str, str, str]) -> str:
    """Число со словом: `counted(3, ("борт", "борта", "бортов"))` → «3 борта»."""
    return f"{count} {plural(count, forms)}"


def duration_text(seconds: float) -> str:
    """Длительность для человека: «12 ч 5 мин», «45 мин», «3 ч», «меньше минуты».

    Args:
        seconds: длительность, секунды.
    """
    hours, minutes = divmod(round(seconds / 60), 60)
    if hours == 0:
        return f"{minutes} мин" if minutes else "меньше минуты"
    if minutes == 0:
        return f"{hours} ч"
    return f"{hours} ч {minutes} мин"


def field_label(key: str) -> str:
    """Поле ТТХ словами; незнакомое — как есть."""
    return FIELD_LABELS.get(key, key)


def gap_text(gap: str) -> str:
    """Пробел каталога для человека: «focal_length_mm: …» → «Фокусное расстояние: …».

    Пробелы в каталоге начинаются с имени поля — по нему интерфейс понимает, к
    какому числу относится пробел. Человеку вместо имени поля нужна подпись.
    """
    key, sep, rest = gap.partition(":")
    if sep and key.strip() in FIELD_LABELS:
        label = FIELD_LABELS[key.strip()]
        return f"{label[:1].upper()}{label[1:]}: {rest.strip()}"
    return gap


def board_label(vehicle_name: str, vehicle_instance: int) -> str:
    """Подпись борта: «Геоскан Gemini #3» (экземпляр — с единицы)."""
    return f"{vehicle_name} #{vehicle_instance + 1}"


def sortie_label(sortie: Sortie, names: Mapping[str, str] | None = None) -> str:
    """Вылет для человека: «Геоскан Gemini #3, вылет 2».

    Args:
        sortie: вылет.
        names: название модели по идентификатору; нет в словаре — идентификатор.
    """
    name = (names or {}).get(sortie.vehicle_id, sortie.vehicle_id)
    return f"{board_label(name, sortie.vehicle_instance)}, вылет {sortie.trip_index + 1}"


def site_label(site_id: str, names: Mapping[str, str] | None = None) -> str:
    """Площадка для человека — после слова «площадка»: «до площадки «Юг»».

    Название вида «Площадка «Юг»» теряет повтор слова, а без своих кавычек
    берётся в кавычки: «Южная площадка» → «до площадки «Южная площадка»».

    Args:
        site_id: идентификатор площадки.
        names: название площадки по идентификатору; нет в словаре — идентификатор.
    """
    name = (names or {}).get(site_id) or site_id
    if name.lower().startswith("площадка "):
        name = name[len("площадка ") :].strip()
    return name if "«" in name else f"«{name}»"


def lower_first(text: str) -> str:
    """Текст с маленькой буквы — для вставки после тире."""
    return text[:1].lower() + text[1:] if text else text


__all__ = [
    "FIELD_LABELS",
    "MODE_LABELS",
    "PHASE_LABELS",
    "SITE_KIND_LABELS",
    "SURVEY_TYPE_LABELS",
    "board_label",
    "counted",
    "duration_text",
    "field_label",
    "gap_text",
    "lower_first",
    "num",
    "plural",
    "site_label",
    "sortie_label",
]
