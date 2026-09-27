"""Диагностика невыполнимости и рекомендации (E15).

Заказчик (№33, №42): задание либо выполнено полностью, либо невыполнимо.
Минимум — сказать об этом; лучше — сказать, чего не хватает: дополнительной
площадки, ещё борта, другой модели, окончания временного запрета.
Пример заказчика: «невыполнимо, потому что самая удалённая точка, которую
нужно снять, слишком далеко».

ЗАГЛУШКА E15: рекомендации строятся по кодам причин простыми правилами.
Место дополнительной площадки — внутренняя точка недоступных подобластей, без
оптимизации. Подбор площадок для всей области (№28) не делается.
"""

from __future__ import annotations

import math

from shapely.ops import unary_union

from core import assumptions
from core.flight.cost import endurance_limit_s
from core.geo.mask import WorkArea
from core.geo.project import polygon_to_shapely
from core.schemas import (
    Decomposition,
    MetricPoint,
    PlanWarning,
    Recommendation,
    RecommendationKind,
    Scenario,
    Severity,
    Stage,
    SurveyPlan,
    Vehicle,
)

_M_PER_KM = 1000.0


def _reach_km(vehicle: Vehicle, transit_speed_mps: float, altitude_m: float) -> float:
    """Радиус действия борта: туда и обратно на одном заряде с отсечкой, км.

    Оценка сверху — без времени на съёмку: сколько борт пролетит от площадки
    и вернётся, если только летит.
    """
    overhead_s = (
        assumptions.TAKEOFF_TIME_S
        + assumptions.LANDING_TIME_S
        + altitude_m / assumptions.CLIMB_RATE_MPS
        + altitude_m / assumptions.DESCENT_RATE_MPS
    )
    flying_s = max(endurance_limit_s(vehicle) - overhead_s, 0.0)
    return flying_s * transit_speed_mps / 2.0 / _M_PER_KM


def _cells(decomposition: Decomposition, warning: PlanWarning) -> list[str]:
    ids = warning.context.get("cell_ids", "")
    known = {cell.id for cell in decomposition.cells}
    return [item for item in ids.split(",") if item in known]


def _out_of_reach(
    scenario: Scenario,
    work_area: WorkArea,
    decomposition: Decomposition,
    survey_plan: SurveyPlan,
    vehicles: dict[str, Vehicle],
    cell_ids: list[str],
) -> list[Recommendation]:
    """Рекомендации для подобластей вне радиуса действия."""
    by_id = {cell.id: cell for cell in decomposition.cells}
    sites = [work_area.site(site.id) for site in scenario.primary_sites()]

    def nearest_site_km(point: MetricPoint) -> float:
        return min(math.hypot(point.x_m - s.x_m, point.y_m - s.y_m) for s in sites) / _M_PER_KM

    farthest = max(cell_ids, key=lambda cell_id: nearest_site_km(by_id[cell_id].centroid))
    distance_km = nearest_site_km(by_id[farthest].centroid)
    reaches = {
        params.vehicle_id: _reach_km(
            vehicles[params.vehicle_id], params.transit_speed_mps, params.altitude_agl_m
        )
        for params in survey_plan.per_vehicle
    }
    reach_text = ", ".join(
        f"{vehicles[vehicle_id].name} — около {reach:.1f} км"
        for vehicle_id, reach in sorted(reaches.items())
    )

    region = unary_union([polygon_to_shapely(by_id[cell_id].polygon) for cell_id in cell_ids])
    spot = region.representative_point()
    return [
        Recommendation(
            kind=RecommendationKind.ADD_SITE,
            message=(
                f"Самая удалённая из таких подобластей — в {distance_km:.1f} км от ближайшей "
                f"площадки. Радиус действия с отсечкой "
                f"{assumptions.BATTERY_RESERVE_FRACTION * 100:.0f}% без учёта съёмки: "
                f"{reach_text}; на съёмку остаётся тем меньше, чем дальше подобласть и "
                "сильнее ветер. Добавьте площадку ближе к ним — предлагаемое место "
                "отмечено на карте"
            ),
            position=MetricPoint(x_m=spot.x, y_m=spot.y),
        ),
        Recommendation(
            kind=RecommendationKind.OTHER_MODEL,
            message="Либо используйте борт с большей автономностью (самолётного типа)",
        ),
    ]


def diagnose(
    scenario: Scenario,
    work_area: WorkArea,
    survey_plan: SurveyPlan,
    decomposition: Decomposition | None,
    reasons: list[PlanWarning],
    vehicles: dict[str, Vehicle],
) -> list[Recommendation]:
    """Подобрать рекомендации к причинам невыполнимости.

    Args:
        scenario: сценарий.
        work_area: рабочая область.
        survey_plan: параметры съёмки.
        decomposition: подобласти; None — конвейер остановился раньше.
        reasons: причины невыполнимости (ошибки стадий и нарушения валидатора).
        vehicles: каталог бортов.

    Returns:
        Рекомендации без повторов, в порядке причин.
    """
    recommendations: list[Recommendation] = []
    codes = {reason.code for reason in reasons}

    if not survey_plan.per_vehicle:
        if codes & {"survey.wind_over_limit", "survey.wind_speed_band_empty"}:
            recommendations.append(
                Recommendation(
                    kind=RecommendationKind.WAIT,
                    message=(
                        f"Ветер {scenario.wind.speed_mps:.1f} м/с не позволяет снимать ни одному "
                        "борту флота — перенесите работы на день со слабым ветром"
                    ),
                )
            )
        recommendations.append(
            Recommendation(
                kind=RecommendationKind.OTHER_MODEL,
                message=(
                    f"Нужен борт с камерой для съёмки типа {scenario.survey_type.value}, "
                    "оптика которой известна (угол поля зрения или фокусное и матрица)"
                ),
            )
        )

    for reason in reasons:
        if reason.code == "allocate.cells_out_of_reach" and decomposition is not None:
            cell_ids = _cells(decomposition, reason)
            if cell_ids and survey_plan.per_vehicle:
                recommendations.extend(
                    _out_of_reach(
                        scenario, work_area, decomposition, survey_plan, vehicles, cell_ids
                    )
                )
        elif reason.code == "allocate.cells_unserviceable":
            recommendations.append(
                Recommendation(
                    kind=RecommendationKind.ADD_VEHICLE,
                    message=(
                        "Добавьте мультиротор: подобласти у запретных зон и препятствий "
                        "самолёт снять не может — ему негде развернуться"
                    ),
                )
            )
        elif reason.code == "allocate.no_trips_left":
            recommendations.append(
                Recommendation(
                    kind=RecommendationKind.ADD_BATTERIES,
                    message="Снимите предел числа вылетов (комплекты АКБ) или добавьте борт",
                )
            )
        elif reason.code in ("validate.deadline", "allocate.deadline_short"):
            recommendations.append(
                Recommendation(
                    kind=RecommendationKind.ADD_VEHICLE,
                    message="Добавьте борт или ослабьте ограничение по сроку",
                )
            )
        elif reason.code in ("validate.separation", "deconflict.unresolved"):
            recommendations.append(
                Recommendation(
                    kind=RecommendationKind.RELAX_REQUIREMENT,
                    message=(
                        "Разнесите борта по разным площадкам или поднимите верхнюю границу "
                        "коридора высот — эшелонов станет больше"
                    ),
                )
            )
        elif reason.code == "validate.no_fly_zone":
            recommendations.append(
                Recommendation(
                    kind=RecommendationKind.ADD_SITE,
                    message=(
                        "Маршрут входит в запретную зону: площадка или подобласть внутри зоны "
                        "с буфером. Вынесите площадку за пределы зоны"
                    ),
                )
            )
        elif reason.code == "validate.corridor":
            recommendations.append(
                Recommendation(
                    kind=RecommendationKind.RELAX_REQUIREMENT,
                    message="Поднимите верхнюю границу коридора высот",
                )
            )

    unique: list[Recommendation] = []
    seen: set[tuple[str, str]] = set()
    for item in recommendations:
        key = (item.kind.value, item.message)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def stub_warning() -> PlanWarning:
    """Признание заглушки E15 — добавляется, когда рекомендации строились."""
    return PlanWarning(
        code="stub.advise",
        severity=Severity.WARNING,
        stage=Stage.ADVISE,
        message=(
            "Рекомендации построены простыми правилами по кодам причин; место площадки "
            "не оптимизируется, подбор площадок для всей области не делается (TODO(E15))"
        ),
    )


__all__ = ["diagnose", "stub_warning"]
