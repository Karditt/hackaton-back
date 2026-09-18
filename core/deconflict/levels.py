"""Эшелоны перелёта внутри коридора высот (заказчик, №10).

Съёмка ведётся на высоте из GSD; на перелётах борта разводятся по высоте:
каждому борту — свой эшелон выше самой высокой съёмки флота, с шагом
`VERTICAL_SEPARATION_M`, не выше коридора и потолка борта. Пример заказчика:
съёмка 150 м, коридор до 700 м — места хватает на полтора десятка эшелонов.
"""

from __future__ import annotations

import math

from core import assumptions
from core.allocate.fleet import FleetInstance
from core.schemas import PlanWarning, Severity, Stage, SurveyPlan


def assign_transit_levels(
    instances: list[FleetInstance], survey_plan: SurveyPlan, corridor_max_m: float
) -> tuple[dict[tuple[str, int], float], list[PlanWarning]]:
    """Назначить каждому борту эшелон перелёта.

    Args:
        instances: физические борта.
        survey_plan: параметры съёмки (высоты).
        corridor_max_m: верхняя граница коридора, м AGL.

    Returns:
        Пара «эшелон по ключу борта (модель, экземпляр), предупреждения».
        Если эшелонов меньше, чем бортов, они используются повторно, и
        разводить такие борта остаётся расписанию.
    """
    warnings: list[PlanWarning] = []
    survey_top = max(params.altitude_agl_m for params in survey_plan.per_vehicle)
    base = survey_top + assumptions.VERTICAL_SEPARATION_M
    count = max(0, math.floor((corridor_max_m - base) / assumptions.VERTICAL_SEPARATION_M) + 1)
    levels = [base + index * assumptions.VERTICAL_SEPARATION_M for index in range(count)]

    if not levels:
        warnings.append(
            PlanWarning(
                code="deconflict.no_transit_levels",
                severity=Severity.WARNING,
                stage=Stage.SCHEDULE,
                message=(
                    f"В коридоре до {corridor_max_m:.0f} м нет места для эшелона перелёта выше "
                    f"съёмки ({survey_top:.0f} м): перелёты идут на высоте съёмки, разведение "
                    "только по времени"
                ),
            )
        )
    elif len(levels) < len(instances):
        warnings.append(
            PlanWarning(
                code="deconflict.levels_shared",
                severity=Severity.WARNING,
                stage=Stage.SCHEDULE,
                message=(
                    f"Эшелонов в коридоре {len(levels)}, бортов {len(instances)}: часть бортов "
                    "делит эшелон, их разводит расписание"
                ),
            )
        )

    assigned: dict[tuple[str, int], float] = {}
    used: dict[float, int] = {level: 0 for level in levels}
    # Борта с низким потолком — первыми: им доступно меньше эшелонов.
    order = sorted(instances, key=lambda item: item.vehicle.max_altitude_agl_m or corridor_max_m)
    for instance in order:
        params = survey_plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        ceiling = instance.vehicle.max_altitude_agl_m or corridor_max_m
        allowed = [level for level in levels if level <= ceiling]
        if not allowed:
            assigned[instance.key] = params.altitude_agl_m
            warnings.append(
                PlanWarning(
                    code="deconflict.level_above_ceiling",
                    severity=Severity.WARNING,
                    stage=Stage.SCHEDULE,
                    message=(
                        f"{instance.label}: все эшелоны выше потолка борта {ceiling:.0f} м — "
                        "перелёт на высоте съёмки"
                    ),
                    context={"vehicle_id": instance.vehicle.id},
                )
            )
            continue
        # Наименее занятый эшелон, при равенстве — нижний: пока эшелонов хватает,
        # у каждого борта свой.
        level = min(allowed, key=lambda item: (used[item], item))
        used[level] += 1
        assigned[instance.key] = level
    return assigned, warnings


__all__ = ["assign_transit_levels"]
