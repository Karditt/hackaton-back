"""Сенсорная модель: высота, полоса захвата, шаг галсов, скорость съёмки.

ЗАГЛУШКА E2 с честной оптикой. Формулы ниже — школьная геометрия центральной
проекции, они верны; заглушечное здесь другое — отсутствие пресетов по типам
съёмки и упрощённая матрица совместимости. См. TODO(E2).
"""

from __future__ import annotations

from core import assumptions
from core.errors import InfeasibleError
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

_MM_PER_M = 1000.0

#: Матрица совместимости «тип съёмки → допустимые классы нагрузки».
#:
#: TODO(E2): таблица плоская, тогда как совместимость зависит ещё и от борта.
#: Открытый вопрос из docs/current_state.md (долг №9): Геоскан 801 несёт
#: зум-камеру на подвесе и для площадной фотограмметрии не предназначен —
#: здесь он отсекается именно потому, что его нагрузка имеет класс
#: INSPECTION_RGB, а не RGB.
COMPATIBLE_PAYLOADS: dict[SurveyType, tuple[PayloadKind, ...]] = {
    SurveyType.RGB: (PayloadKind.RGB,),
    SurveyType.MULTISPECTRAL: (PayloadKind.MULTISPECTRAL,),
    SurveyType.IR: (PayloadKind.IR,),
    SurveyType.LIDAR: (PayloadKind.LIDAR,),
    SurveyType.GEOPHYSICAL: (PayloadKind.GEOPHYSICAL,),
}


def _has_optics(payload: Payload) -> bool:
    """True, если нагрузки хватает для расчёта GSD."""
    return (
        payload.focal_length_mm is not None
        and payload.sensor_width_mm is not None
        and payload.image_width_px is not None
        and payload.image_height_px is not None
    )


def pixel_size_mm(payload: Payload) -> float:
    """Размер пикселя матрицы.

    Args:
        payload: нагрузка с заполненными размерами матрицы и разрешением.

    Returns:
        Размер пикселя, миллиметры.
    """
    assert payload.sensor_width_mm is not None and payload.image_width_px is not None
    return payload.sensor_width_mm / payload.image_width_px


def altitude_for_gsd(payload: Payload, gsd_m: float) -> float:
    """Высота съёмки, дающая требуемое разрешение на местности.

    Центральная проекция: GSD = p · H / f, где p — размер пикселя, f —
    фокусное расстояние, H — высота. Единицы p и f сокращаются, поэтому
    результат в тех же единицах, что и GSD.

    Args:
        payload: нагрузка с известной оптикой.
        gsd_m: требуемое разрешение на местности, метры на пиксель.

    Returns:
        Высота съёмки над снимаемой поверхностью, метры.
    """
    assert payload.focal_length_mm is not None
    return gsd_m * payload.focal_length_mm / pixel_size_mm(payload)


def swath_width_m(payload: Payload, gsd_m: float) -> float:
    """Ширина полосы захвата одного кадра поперёк курса.

    Args:
        payload: нагрузка с известной оптикой.
        gsd_m: разрешение на местности, метры на пиксель.

    Returns:
        Ширина захвата, метры.
    """
    assert payload.image_width_px is not None
    return payload.image_width_px * gsd_m


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
        if payload.kind in allowed and _has_optics(payload):
            return payload, None

    return None, PlanWarning(
        code="survey.no_compatible_payload",
        severity=Severity.ERROR,
        stage=Stage.SURVEY,
        message=(
            f"У борта {vehicle.name} нет нагрузки, пригодной для съёмки типа "
            f"{survey_type.value} — борт исключён из планирования"
        ),
        context={"vehicle_id": vehicle.id},
    )


def _params_for(
    vehicle: Vehicle, payload: Payload, scenario: Scenario
) -> tuple[SurveyParams | None, list[PlanWarning]]:
    """Посчитать параметры съёмки одной пары «борт × нагрузка»."""
    warnings: list[PlanWarning] = []
    quality = scenario.quality

    if not _has_optics(payload):
        return None, [
            PlanWarning(
                code="survey.optics_unknown",
                severity=Severity.ERROR,
                stage=Stage.SURVEY,
                message=(
                    f"Оптические параметры нагрузки {payload.id} в официальном источнике "
                    f"отсутствуют ({'; '.join(payload.gaps) or 'пробел не описан'}). "
                    "Инвариант 6 запрещает подставлять правдоподобные числа — "
                    "борт исключён из планирования"
                ),
                context={"vehicle_id": vehicle.id, "payload_id": payload.id},
            )
        ]

    altitude_m = altitude_for_gsd(payload, quality.gsd_m)
    gsd_m = quality.gsd_m

    floor_m = vehicle.min_altitude_agl_m or assumptions.MIN_SURVEY_ALTITUDE_AGL_M
    if altitude_m < floor_m:
        altitude_m = floor_m
        gsd_m = altitude_m * pixel_size_mm(payload) / (payload.focal_length_mm or 1.0)
        warnings.append(
            PlanWarning(
                code="survey.altitude_floor",
                severity=Severity.WARNING,
                stage=Stage.SURVEY,
                message=(
                    f"{vehicle.name}: требуемый GSD {quality.gsd_m * 100:.1f} см/пикс достижим "
                    f"ниже минимальной безопасной высоты {floor_m:.0f} м. Высота поднята, "
                    f"фактический GSD {gsd_m * 100:.1f} см/пикс"
                ),
                context={"vehicle_id": vehicle.id},
            )
        )

    ceiling_m = vehicle.max_altitude_agl_m
    if ceiling_m is not None and altitude_m > ceiling_m:
        altitude_m = ceiling_m
        gsd_m = altitude_m * pixel_size_mm(payload) / (payload.focal_length_mm or 1.0)
        warnings.append(
            PlanWarning(
                code="survey.altitude_ceiling",
                severity=Severity.WARNING,
                stage=Stage.SURVEY,
                message=(
                    f"{vehicle.name}: потолок над землёй {ceiling_m:.0f} м не даёт подняться "
                    f"под требуемый GSD. Фактический GSD {gsd_m * 100:.1f} см/пикс "
                    f"вместо {quality.gsd_m * 100:.1f} см/пикс"
                ),
                context={"vehicle_id": vehicle.id},
            )
        )

    swath_m = swath_width_m(payload, gsd_m)
    spacing_m = swath_m * (1.0 - quality.side_overlap)

    speed_mps = vehicle.cruise_speed_mps
    shot_interval_s: float | None = None
    if payload.min_shot_interval_s:
        base_m = (payload.image_height_px or 0) * gsd_m * (1.0 - quality.forward_overlap)
        speed_limit_mps = base_m / payload.min_shot_interval_s
        if speed_limit_mps < speed_mps:
            speed_mps = speed_limit_mps
            warnings.append(
                PlanWarning(
                    code="survey.speed_limited_by_sensor",
                    severity=Severity.INFO,
                    stage=Stage.SURVEY,
                    message=(
                        f"{vehicle.name}: скорость на галсе ограничена интервалом съёмки "
                        f"до {speed_mps:.1f} м/с"
                    ),
                    context={"vehicle_id": vehicle.id},
                )
            )
        shot_interval_s = (
            (payload.image_height_px or 0) * gsd_m * (1.0 - quality.forward_overlap) / speed_mps
            if speed_mps > 0
            else None
        )

    params = SurveyParams(
        vehicle_id=vehicle.id,
        payload_id=payload.id,
        survey_type=scenario.survey_type,
        altitude_agl_m=altitude_m,
        gsd_m=gsd_m,
        swath_width_m=swath_m,
        strip_spacing_m=spacing_m,
        survey_speed_mps=speed_mps,
        forward_overlap=quality.forward_overlap,
        side_overlap=quality.side_overlap,
        shot_interval_s=shot_interval_s,
    )
    return params, warnings


def build_survey_plan(scenario: Scenario, vehicles: dict[str, Vehicle]) -> SurveyPlan:
    """Построить параметры съёмки для всего флота сценария.

    Args:
        scenario: сценарий (требования к качеству, тип съёмки, состав флота).
        vehicles: каталог бортов по идентификаторам.

    Returns:
        План съёмки: по набору параметров на каждый пригодный борт плюс
        опорный набор, по которому строится покрытие.

    Raises:
        InfeasibleError: ни один борт флота не пригоден для заданной съёмки.
    """
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

        params, params_warnings = _params_for(vehicle, payload, scenario)
        warnings.extend(params_warnings)
        if params is not None:
            per_vehicle.append(params)

    if not per_vehicle:
        raise InfeasibleError(
            f"Ни один борт флота не пригоден для съёмки типа {scenario.survey_type.value}. "
            + " ".join(warning.message for warning in warnings)
        )

    # Опорный борт — с самым узким шагом галсов: покрытие, построенное под
    # него, годится и остальным (у них полоса захвата шире). Обратное неверно.
    # TODO(E3): у разнотипного флота правильнее строить покрытие отдельно под
    # каждый класс бортов, а не приводить всех к самому узкому.
    reference = min(per_vehicle, key=lambda params: params.strip_spacing_m)
    if len({params.strip_spacing_m for params in per_vehicle}) > 1:
        warnings.append(
            PlanWarning(
                code="survey.heterogeneous_spacing",
                severity=Severity.WARNING,
                stage=Stage.SURVEY,
                message=(
                    "Флот разнотипный, шаг галсов у бортов разный. Покрытие построено по "
                    f"самому узкому шагу ({reference.strip_spacing_m:.0f} м, борт "
                    f"{reference.vehicle_id}) — остальные борта снимают с избыточным "
                    "перекрытием. TODO(E3): покрытие по классам бортов"
                ),
            )
        )

    return SurveyPlan(
        per_vehicle=per_vehicle,
        reference_vehicle_id=reference.vehicle_id,
        warnings=warnings,
        stub=True,
    )


__all__ = [
    "COMPATIBLE_PAYLOADS",
    "altitude_for_gsd",
    "build_survey_plan",
    "pixel_size_mm",
    "swath_width_m",
]
