"""Проверки готового плана.

ЗАГЛУШКА E7: проверяется то, что можно проверить по уже посчитанным числам.
Геометрические проверки траектории (выход за воздушное пространство,
задевание запретных зон петлёй разворота, разведение бортов) требуют полной
траектории с петлями, которой у заглушки E4 ещё нет.
"""

from __future__ import annotations

from core import assumptions
from core.flight.cost import sortie_is_feasible
from core.schemas import (
    CoverageSet,
    PlanWarning,
    Scenario,
    Severity,
    Sortie,
    Stage,
    Vehicle,
)


def _wind_checks(scenario: Scenario, vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Сравнить ветер сценария с паспортным пределом каждого борта."""
    warnings: list[PlanWarning] = []
    for vehicle_id in {unit.vehicle_id for unit in scenario.fleet}:
        vehicle = vehicles.get(vehicle_id)
        if vehicle is None:
            continue
        limit_mps = vehicle.max_wind_mps
        safe_mps = limit_mps * assumptions.WIND_SAFETY_FRACTION
        if scenario.wind.speed_mps > limit_mps:
            warnings.append(
                PlanWarning(
                    code="validate.wind_over_limit",
                    severity=Severity.ERROR,
                    stage=Stage.VALIDATE,
                    message=(
                        f"{vehicle.name}: ветер {scenario.wind.speed_mps:.1f} м/с превышает "
                        f"паспортный предел {limit_mps:.1f} м/с — вылет недопустим"
                    ),
                    context={"vehicle_id": vehicle_id},
                )
            )
        elif scenario.wind.speed_mps > safe_mps:
            warnings.append(
                PlanWarning(
                    code="validate.wind_near_limit",
                    severity=Severity.WARNING,
                    stage=Stage.VALIDATE,
                    message=(
                        f"{vehicle.name}: ветер {scenario.wind.speed_mps:.1f} м/с выше "
                        f"{assumptions.WIND_SAFETY_FRACTION * 100:.0f}% паспортного предела "
                        f"{limit_mps:.1f} м/с — запаса на порывы почти нет"
                    ),
                    context={"vehicle_id": vehicle_id},
                )
            )
    return warnings


def _sortie_checks(sorties: list[Sortie], vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Проверить каждый вылет по продолжительности и энергии."""
    warnings: list[PlanWarning] = []
    for sortie in sorties:
        vehicle = vehicles.get(sortie.vehicle_id)
        if vehicle is None:
            continue
        feasible, reason = sortie_is_feasible(sortie, vehicle)
        if not feasible:
            warnings.append(
                PlanWarning(
                    code="validate.sortie_infeasible",
                    severity=Severity.ERROR,
                    stage=Stage.VALIDATE,
                    message=f"Вылет {sortie.id} невыполним: {reason}",
                    context={"sortie_id": sortie.id, "vehicle_id": sortie.vehicle_id},
                )
            )
    return warnings


def _coverage_checks(coverage: CoverageSet, sorties: list[Sortie]) -> list[PlanWarning]:
    """Сверить, что все построенные галсы попали хоть в один вылет."""
    planned = {strip.id for strip in coverage.strips}
    covered = {strip_id for sortie in sorties for strip_id in sortie.strip_ids}
    missing = planned - covered
    if not missing:
        return []
    return [
        PlanWarning(
            code="validate.strips_not_flown",
            severity=Severity.ERROR,
            stage=Stage.VALIDATE,
            message=(
                f"{len(missing)} из {len(planned)} галсов не вошли ни в один вылет — "
                "часть области останется неснятой"
            ),
            context={"strip_ids": ",".join(sorted(missing)[:20])},
        )
    ]


def _catalog_gap_checks(scenario: Scenario, vehicles: dict[str, Vehicle]) -> list[PlanWarning]:
    """Сообщить, какие числа плана выведены допущением, а не взяты из ТТХ.

    Инвариант 6 требует, чтобы пробел был виден. План, посчитанный на
    допущениях, обязан сказать об этом, а не выглядеть паспортным.
    """
    warnings: list[PlanWarning] = []
    for vehicle_id in sorted({unit.vehicle_id for unit in scenario.fleet}):
        vehicle = vehicles.get(vehicle_id)
        if vehicle is None or not vehicle.derived_fields:
            continue
        warnings.append(
            PlanWarning(
                code="validate.derived_specs",
                severity=Severity.INFO,
                stage=Stage.VALIDATE,
                message=(
                    f"{vehicle.name}: поля {', '.join(vehicle.derived_fields)} выведены "
                    "допущениями команды (core/assumptions.py), в официальном документе "
                    "их нет"
                ),
                context={"vehicle_id": vehicle_id},
            )
        )
    return warnings


def validate_mission(
    scenario: Scenario,
    coverage: CoverageSet,
    sorties: list[Sortie],
    vehicles: dict[str, Vehicle],
) -> list[PlanWarning]:
    """Проверить готовый план.

    Args:
        scenario: исходный сценарий.
        coverage: построенное покрытие.
        sorties: собранные вылеты.
        vehicles: каталог бортов.

    Returns:
        Список предупреждений. Пустой список означает «проверенное прошло»,
        а не «план безупречен»: перечень нереализованных проверок — в
        докстринге пакета.
    """
    warnings: list[PlanWarning] = []
    warnings.extend(_wind_checks(scenario, vehicles))
    warnings.extend(_sortie_checks(sorties, vehicles))
    warnings.extend(_coverage_checks(coverage, sorties))
    warnings.extend(_catalog_gap_checks(scenario, vehicles))

    # TODO(E7): геометрические проверки траектории. Ниже — явное признание,
    # а не молчание: план, помеченный как проверенный, но без этих проверок,
    # опаснее непроверенного.
    warnings.append(
        PlanWarning(
            code="stub.validate",
            severity=Severity.WARNING,
            stage=Stage.VALIDATE,
            message=(
                "Не проверены: выход траектории за границу воздушного пространства, "
                "задевание запретных зон петлями разворотов, достижимость резервной "
                "площадки, разведение бортов в воздухе (TODO(E7))"
            ),
        )
    )
    return warnings


__all__ = ["validate_mission"]
