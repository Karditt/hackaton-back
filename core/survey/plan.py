"""Сенсорная модель: высота, полоса захвата, шаг галсов, путевые скорости.

Модель — та, что описал заказчик (№26): качество задаётся GSD; по GSD, углу
поля зрения и разрешению матрицы считается высота съёмки над поверхностью.
Своей физики на каждый тип съёмки нет и не будет — заказчик назвал это
излишним. Перекрытия — из конфига типа съёмки (№25).

Ветер здесь определяет путевые скорости (№45): на галсе автопилот держит
постоянную путевую скорость, и она выбирается такой, чтобы борт удержал её на
любом курсе — против ветра воздушная скорость не выше максимальной, по ветру
у самолёта не ниже минимальной. Борт, для которого такой скорости нет,
исключается с объяснением.
"""

from __future__ import annotations

import math

from core import assumptions
from core.schemas import (
    FleetUnit,
    Payload,
    PayloadKind,
    PlanWarning,
    Scenario,
    Severity,
    Stage,
    SurveyParams,
    SurveyPlan,
    SurveyType,
    Vehicle,
)
from core.survey.presets import resolve_overlaps

#: Матрица совместимости «тип съёмки → допустимые классы нагрузки».
#: Разные модели камер одного типа в одном задании допустимы (заказчик, №27).
#: Геоскан 801 несёт зум-камеру на подвесе (класс INSPECTION_RGB) и для
#: площадной RGB-съёмки не предназначен — отсекается здесь.
COMPATIBLE_PAYLOADS: dict[SurveyType, tuple[PayloadKind, ...]] = {
    SurveyType.RGB: (PayloadKind.RGB,),
    SurveyType.MULTISPECTRAL: (PayloadKind.MULTISPECTRAL,),
    SurveyType.IR: (PayloadKind.IR,),
    SurveyType.LIDAR: (PayloadKind.LIDAR,),
    SurveyType.GEOPHYSICAL: (PayloadKind.GEOPHYSICAL,),
}


def has_optics(payload: Payload) -> bool:
    """True, если нагрузки хватает для расчёта высоты по GSD.

    Нужна ширина кадра в пикселях и либо угол поля зрения, либо пара
    «фокусное расстояние + ширина матрицы».
    """
    if payload.image_width_px is None:
        return False
    if payload.fov_horizontal_deg is not None:
        return True
    return payload.focal_length_mm is not None and payload.sensor_width_mm is not None


def _ground_width_per_altitude(payload: Payload) -> float:
    """Ширина кадра на местности на метр высоты, безразмерная.

    По углу поля зрения: 2·tg(θ/2). По оптике: ширина матрицы / фокусное.
    Это одно и то же отношение, записанное через разные паспортные величины.
    """
    if payload.fov_horizontal_deg is not None:
        return 2.0 * math.tan(math.radians(payload.fov_horizontal_deg) / 2.0)
    assert payload.sensor_width_mm is not None and payload.focal_length_mm is not None
    return payload.sensor_width_mm / payload.focal_length_mm


def altitude_for_gsd(payload: Payload, gsd_m: float) -> float:
    """Высота съёмки, дающая требуемое разрешение на местности.

    Центральная проекция: ширина кадра на местности W = GSD · N и одновременно
    W = H · k, где k — ширина кадра на метр высоты. Отсюда H = GSD · N / k.

    Args:
        payload: нагрузка с известной оптикой (`has_optics`).
        gsd_m: требуемое разрешение на местности, метры на пиксель.

    Returns:
        Высота съёмки над поверхностью, метры.
    """
    assert payload.image_width_px is not None
    return gsd_m * payload.image_width_px / _ground_width_per_altitude(payload)


def gsd_for_altitude(payload: Payload, altitude_m: float) -> float:
    """Разрешение на местности на заданной высоте, метры на пиксель."""
    assert payload.image_width_px is not None
    return altitude_m * _ground_width_per_altitude(payload) / payload.image_width_px


def swath_width_m(payload: Payload, gsd_m: float) -> float:
    """Ширина полосы захвата одного кадра поперёк курса, метры."""
    assert payload.image_width_px is not None
    return payload.image_width_px * gsd_m


def ground_speed_band_mps(vehicle: Vehicle, wind_speed_mps: float) -> tuple[float, float]:
    """Диапазон путевых скоростей, которые борт удержит на любом курсе.

    Против ветра воздушная скорость равна V + W и не должна превышать
    максимальную; по ветру равна V − W и у самолёта не должна опускаться ниже
    минимальной. Проверка по худшему курсу: направление галсов выбирается
    позже (E3), и скорость должна подходить к любому.

    Args:
        vehicle: борт.
        wind_speed_mps: скорость ветра, м/с.

    Returns:
        Пара «нижняя, верхняя» граница путевой скорости, м/с. Пустой диапазон
        (нижняя > верхней) — борт при этом ветре снимать не может.
    """
    airspeed_max = vehicle.max_speed_mps or vehicle.cruise_speed_mps
    upper = airspeed_max - wind_speed_mps
    lower = 0.0
    if vehicle.is_fixed_wing() and vehicle.min_speed_mps is not None:
        lower = vehicle.min_speed_mps + wind_speed_mps
    return lower, upper


def _select_payload(
    vehicle: Vehicle, unit: FleetUnit, survey_type: SurveyType
) -> tuple[Payload | None, PlanWarning | None]:
    """Выбрать нагрузку борта под тип съёмки."""
    allowed = COMPATIBLE_PAYLOADS[survey_type]

    if unit.payload_id is not None:
        payload = vehicle.payload(unit.payload_id)
        if payload is None:
            return None, PlanWarning(
                code="survey.payload_unknown",
                severity=Severity.ERROR,
                stage=Stage.SURVEY,
                message=f"У борта {vehicle.id} нет нагрузки {unit.payload_id!r}",
                context={"vehicle_id": vehicle.id},
            )
        if payload.kind not in allowed:
            return None, PlanWarning(
                code="survey.payload_incompatible",
                severity=Severity.ERROR,
                stage=Stage.SURVEY,
                message=(
                    f"Нагрузка {payload.id} (класс {payload.kind.value}) не подходит для "
                    f"съёмки типа {survey_type.value}"
                ),
                context={"vehicle_id": vehicle.id, "payload_id": payload.id},
            )
        return payload, None

    for payload in vehicle.payloads:
        if payload.kind in allowed and has_optics(payload):
            return payload, None

    return None, PlanWarning(
        code="survey.no_compatible_payload",
        severity=Severity.ERROR,
        stage=Stage.SURVEY,
        message=(
            f"У борта {vehicle.name} нет нагрузки, пригодной для съёмки типа "
            f"{survey_type.value}, — борт исключён из планирования"
        ),
        context={"vehicle_id": vehicle.id},
    )


def _excluded(vehicle: Vehicle, code: str, message: str, **context: str) -> PlanWarning:
    """Предупреждение об исключении борта из планирования."""
    return PlanWarning(
        code=code,
        severity=Severity.ERROR,
        stage=Stage.SURVEY,
        message=message,
        context={"vehicle_id": vehicle.id, **context},
    )


def _params_for(
    vehicle: Vehicle,
    payload: Payload,
    scenario: Scenario,
    forward_overlap: float,
    side_overlap: float,
) -> tuple[SurveyParams | None, list[PlanWarning]]:
    """Посчитать параметры съёмки одной пары «борт × нагрузка»."""
    warnings: list[PlanWarning] = []
    quality = scenario.quality
    wind_mps = scenario.wind.speed_mps

    if not has_optics(payload):
        return None, [
            _excluded(
                vehicle,
                "survey.optics_unknown",
                (
                    f"{vehicle.name}: у нагрузки {payload.id} в официальном источнике нет "
                    f"оптики для расчёта высоты по GSD "
                    f"({'; '.join(payload.gaps) or 'пробел не описан'}). Инвариант 6 "
                    "запрещает подставлять правдоподобные числа — борт исключён"
                ),
                payload_id=payload.id,
            )
        ]

    if wind_mps > vehicle.max_wind_mps:
        return None, [
            _excluded(
                vehicle,
                "survey.wind_over_limit",
                (
                    f"{vehicle.name}: ветер {wind_mps:.1f} м/с выше паспортного предела "
                    f"{vehicle.max_wind_mps:.1f} м/с — борт исключён"
                ),
            )
        ]

    altitude_m = altitude_for_gsd(payload, quality.gsd_m)
    gsd_m = quality.gsd_m

    floor_m = vehicle.min_altitude_agl_m or assumptions.MIN_SURVEY_ALTITUDE_AGL_M
    ceilings = [scenario.altitude_corridor.max_altitude_agl_m - assumptions.VERTICAL_SEPARATION_M]
    if vehicle.max_altitude_agl_m is not None:
        ceilings.append(vehicle.max_altitude_agl_m)
    ceiling_m = min(ceilings)

    if floor_m > ceiling_m:
        return None, [
            _excluded(
                vehicle,
                "survey.no_altitude_room",
                (
                    f"{vehicle.name}: минимальная высота {floor_m:.0f} м выше допустимой "
                    f"{ceiling_m:.0f} м (коридор до "
                    f"{scenario.altitude_corridor.max_altitude_agl_m:.0f} м минус эшелон "
                    "перелёта) — борт исключён"
                ),
            )
        ]

    if altitude_m < floor_m:
        altitude_m = floor_m
        gsd_m = gsd_for_altitude(payload, altitude_m)
        warnings.append(
            PlanWarning(
                code="survey.altitude_floor",
                severity=Severity.WARNING,
                stage=Stage.SURVEY,
                message=(
                    f"{vehicle.name}: требуемый GSD {quality.gsd_m * 100:.1f} см/пикс достижим "
                    f"ниже минимальной безопасной высоты {floor_m:.0f} м. Высота поднята, "
                    f"фактический GSD {gsd_m * 100:.1f} см/пикс — грубее требуемого"
                ),
                context={"vehicle_id": vehicle.id},
            )
        )
    elif altitude_m > ceiling_m:
        altitude_m = ceiling_m
        gsd_m = gsd_for_altitude(payload, altitude_m)
        warnings.append(
            PlanWarning(
                code="survey.altitude_ceiling",
                severity=Severity.INFO,
                stage=Stage.SURVEY,
                message=(
                    f"{vehicle.name}: высота под требуемый GSD выше допустимой "
                    f"{ceiling_m:.0f} м (потолок борта или коридор минус эшелон перелёта). "
                    f"Съёмка ниже, фактический GSD {gsd_m * 100:.1f} см/пикс — точнее "
                    "требуемого, галсов больше"
                ),
                context={"vehicle_id": vehicle.id},
            )
        )

    swath_m = swath_width_m(payload, gsd_m)
    spacing_m = swath_m * (1.0 - side_overlap)

    lower_mps, upper_mps = ground_speed_band_mps(vehicle, wind_mps)
    survey_cap_mps = upper_mps
    shot_interval_s: float | None = None
    if payload.min_shot_interval_s and payload.image_height_px:
        base_m = payload.image_height_px * gsd_m * (1.0 - forward_overlap)
        survey_cap_mps = min(survey_cap_mps, base_m / payload.min_shot_interval_s)

    if survey_cap_mps <= 0.0 or survey_cap_mps < lower_mps:
        return None, [
            _excluded(
                vehicle,
                "survey.wind_speed_band_empty",
                (
                    f"{vehicle.name}: при ветре {wind_mps:.1f} м/с нет путевой скорости, "
                    f"которую борт удержит на любом курсе (нужно от {lower_mps:.1f} до "
                    f"{survey_cap_mps:.1f} м/с) — борт исключён. Выбор направления галсов "
                    "поперёк ветра (E3) может расширить диапазон"
                ),
            )
        ]

    survey_mps = min(max(vehicle.cruise_speed_mps, lower_mps), survey_cap_mps)
    transit_mps = min(max(vehicle.cruise_speed_mps, lower_mps), upper_mps)
    if survey_mps < vehicle.cruise_speed_mps:
        warnings.append(
            PlanWarning(
                code="survey.speed_reduced",
                severity=Severity.INFO,
                stage=Stage.SURVEY,
                message=(
                    f"{vehicle.name}: путевая скорость на галсе {survey_mps:.1f} м/с вместо "
                    f"крейсерской {vehicle.cruise_speed_mps:.1f} м/с — иначе против ветра "
                    f"{wind_mps:.1f} м/с не хватит воздушной скорости или интервала съёмки"
                ),
                context={"vehicle_id": vehicle.id},
            )
        )
    if payload.image_height_px:
        shot_interval_s = payload.image_height_px * gsd_m * (1.0 - forward_overlap) / survey_mps

    params = SurveyParams(
        vehicle_id=vehicle.id,
        payload_id=payload.id,
        survey_type=scenario.survey_type,
        altitude_agl_m=altitude_m,
        gsd_m=gsd_m,
        swath_width_m=swath_m,
        strip_spacing_m=spacing_m,
        survey_speed_mps=survey_mps,
        transit_speed_mps=transit_mps,
        forward_overlap=forward_overlap,
        side_overlap=side_overlap,
        shot_interval_s=shot_interval_s,
    )
    return params, warnings


def build_survey_plan(scenario: Scenario, vehicles: dict[str, Vehicle]) -> SurveyPlan:
    """Построить параметры съёмки для всего флота сценария.

    Args:
        scenario: сценарий (требования к качеству, тип съёмки, ветер, коридор, флот).
        vehicles: каталог бортов по идентификаторам.

    Returns:
        План съёмки: по набору параметров на каждый пригодный борт. Если
        пригодных нет, `per_vehicle` пуст, а причины — в предупреждениях
        уровня ERROR: конвейер превращает это в отчёт о невыполнимости.

    Raises:
        ScenarioError: перекрытие сценария ниже минимума для типа съёмки.
    """
    forward_overlap, side_overlap, _preset = resolve_overlaps(
        scenario.survey_type, scenario.quality
    )
    per_vehicle: list[SurveyParams] = []
    warnings: list[PlanWarning] = []
    seen: set[str] = set()

    for unit in scenario.fleet:
        if unit.vehicle_id in seen:
            continue
        seen.add(unit.vehicle_id)
        vehicle = vehicles[unit.vehicle_id]

        payload, problem = _select_payload(vehicle, unit, scenario.survey_type)
        if payload is None:
            if problem is not None:
                warnings.append(problem)
            continue

        params, params_warnings = _params_for(
            vehicle, payload, scenario, forward_overlap, side_overlap
        )
        warnings.extend(params_warnings)
        if params is not None:
            per_vehicle.append(params)

    return SurveyPlan(per_vehicle=per_vehicle, warnings=warnings, stub=False)


__all__ = [
    "COMPATIBLE_PAYLOADS",
    "altitude_for_gsd",
    "build_survey_plan",
    "gsd_for_altitude",
    "ground_speed_band_mps",
    "has_optics",
    "swath_width_m",
]
