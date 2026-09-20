"""Эшелоны перелёта внутри коридора высот (заказчик, №10).

Съёмка ведётся на высоте из GSD; на перелётах борта разводятся по высоте.
Эшелон борта отсчитывается **от его собственной высоты съёмки**: коптеру,
снимающему на 204 м, незачем подниматься к эшелону самолёта, снимающего на
456 м. Набор и снижение — это время и заряд: в `medium` перелёт на общем
эшелоне съедал у коптера 27–33% вылета (долг №34).

Разведение обеспечивают два условия, а не общая база:

* эшелон отстоит не меньше `VERTICAL_SEPARATION_M` от высоты съёмки **любого**
  борта флота — перелетающий борт не пройдёт вплотную к чужой съёмке;
* эшелон отстоит не меньше того же интервала от эшелонов других бортов —
  пока в коридоре под потолком борта есть место.

Места не осталось — борта делят эшелон, и разводит их расписание: это
объявляется предупреждением. Пример заказчика: съёмка 150 м, коридор до
700 м — места хватает на полтора десятка эшелонов.
"""

from __future__ import annotations

from core import assumptions
from core.allocate.fleet import FleetInstance
from core.schemas import PlanWarning, Severity, Stage, SurveyPlan


def _clear_of(level_m: float, others: list[float]) -> bool:
    """Отстоит ли высота от каждой из `others` не меньше вертикального интервала."""
    step = assumptions.VERTICAL_SEPARATION_M
    return all(abs(level_m - other) >= step - assumptions.GEOMETRY_TOLERANCE_M for other in others)


def _candidates(
    own_m: float, limit_m: float, survey_altitudes: list[float], taken: list[float]
) -> list[float]:
    """Эшелоны, доступные борту: от его высоты съёмки вверх до предела.

    Сетка строится от собственной высоты съёмки; к ней добавляются эшелоны,
    уже занятые другими бортами, — на них борт может встать вторым, если
    своего места не осталось. Промежуточная высота в 9 м от чужого эшелона
    хуже делёжа: делёж разводит расписание, а такое соседство не разводит
    никто.

    Args:
        own_m: высота съёмки этого борта, м AGL.
        limit_m: верхний предел — потолок борта или коридор, м AGL.
        survey_altitudes: высоты съёмки всего флота, м AGL.
        taken: эшелоны, уже назначенные другим бортам, м AGL.

    Returns:
        Высоты эшелонов снизу вверх; пустой список, если места нет.
    """
    step = assumptions.VERTICAL_SEPARATION_M
    tolerance = assumptions.GEOMETRY_TOLERANCE_M
    grid: list[float] = []
    level = own_m + step
    while level <= limit_m + tolerance:
        grid.append(level)
        level += step
    return sorted(
        item
        for item in {*grid, *taken}
        if own_m + step - tolerance <= item <= limit_m + tolerance
        and _clear_of(item, survey_altitudes)
    )


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
        Если свободных эшелонов под потолком борта не осталось, эшелон
        достаётся нескольким бортам, и разводить их остаётся расписанию.
    """
    survey_altitudes = sorted({params.altitude_agl_m for params in survey_plan.per_vehicle})

    own: dict[tuple[str, int], float] = {}
    limit: dict[tuple[str, int], float] = {}
    for instance in instances:
        params = survey_plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        own[instance.key] = params.altitude_agl_m
        limit[instance.key] = min(
            corridor_max_m, instance.vehicle.max_altitude_agl_m or corridor_max_m
        )

    warnings: list[PlanWarning] = []
    assigned: dict[tuple[str, int], float] = {}
    on_level: dict[float, list[FleetInstance]] = {}
    # Первыми — те, кому теснее всего: у кого меньше запас между своей съёмкой
    # и своим пределом. Иначе борт с широким запасом занял бы чужое место.
    order = sorted(instances, key=lambda item: (limit[item.key] - own[item.key], item.key))
    for instance in order:
        taken = sorted(on_level)
        available = _candidates(own[instance.key], limit[instance.key], survey_altitudes, taken)
        free = [level for level in available if _clear_of(level, taken)]
        shared = [level for level in available if level in on_level]
        if free:
            level = free[0]
        elif shared:
            # Своего места не осталось — встаём вторым на наименее занятый.
            level = min(shared, key=lambda item: (len(on_level[item]), item))
        else:
            assigned[instance.key] = own[instance.key]
            warnings.append(
                PlanWarning(
                    code="deconflict.level_above_ceiling",
                    severity=Severity.WARNING,
                    stage=Stage.SCHEDULE,
                    message=(
                        f"{instance.label}: между высотой съёмки {own[instance.key]:.0f} м и "
                        f"пределом {limit[instance.key]:.0f} м нет места для эшелона — "
                        "перелёт на высоте съёмки"
                    ),
                    context={"vehicle_id": instance.vehicle.id},
                )
            )
            continue
        assigned[instance.key] = level
        on_level.setdefault(level, []).append(instance)

    if instances and not on_level:
        warnings.append(
            PlanWarning(
                code="deconflict.no_transit_levels",
                severity=Severity.WARNING,
                stage=Stage.SCHEDULE,
                message=(
                    f"В коридоре до {corridor_max_m:.0f} м нет места ни для одного эшелона "
                    "перелёта выше съёмки: перелёты идут на высоте съёмки, разведение "
                    "только по времени"
                ),
            )
        )
    warnings.extend(_shared_level_warnings(on_level, own, limit, survey_altitudes))
    return assigned, warnings


def _shared_level_warnings(
    on_level: dict[float, list[FleetInstance]],
    own: dict[tuple[str, int], float],
    limit: dict[tuple[str, int], float],
    survey_altitudes: list[float],
) -> list[PlanWarning]:
    """Предупреждения о делёже эшелона — по факту назначения.

    Считать по числу эшелонов в коридоре нельзя: борта делят эшелон из-за
    своего потолка и своей высоты съёмки, а не из-за размера коридора (долг
    №35). Поэтому — по одному предупреждению на каждый эшелон, который
    достался нескольким бортам, с их списком и причиной.

    Args:
        on_level: борта по назначенным эшелонам, м AGL.
        own: высота съёмки каждого борта, м AGL.
        limit: верхний предел каждого борта, м AGL.
        survey_altitudes: высоты съёмки всего флота, м AGL.

    Returns:
        Предупреждения `deconflict.levels_shared`.
    """
    warnings: list[PlanWarning] = []
    for level, group in sorted(on_level.items()):
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda item: item.key)
        tightest = min(group, key=lambda item: limit[item.key] - own[item.key])
        room = len(_candidates(own[tightest.key], limit[tightest.key], survey_altitudes, taken=[]))
        warnings.append(
            PlanWarning(
                code="deconflict.levels_shared",
                severity=Severity.WARNING,
                stage=Stage.SCHEDULE,
                message=(
                    f"Эшелон {level:.0f} м делят {len(group)} бортов "
                    f"({', '.join(item.label for item in group)}): из {room} эшелонов между "
                    f"съёмкой {own[tightest.key]:.0f} м и пределом {limit[tightest.key]:.0f} м "
                    "свободных не осталось — их разводит расписание"
                ),
                context={
                    "level_m": f"{level:.0f}",
                    "board_ids": ",".join(item.board_id for item in group),
                    "room": str(room),
                },
            )
        )
    return warnings


__all__ = ["assign_transit_levels"]
