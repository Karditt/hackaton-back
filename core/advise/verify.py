"""Проверка рекомендаций расчётом (E15, заказчик №33, №42, №28).

Совет «добавьте площадку» или «добавьте борт», выведенный правилом по коду
причины, может не помочь: площадки мало без борта, одного борта мало к сроку.
Поэтому совет проверяется тем же конвейером: сценарий с этой правкой
считается целиком, с краш-тестом, и к совету дописывается итог — задание с
ней выполнимо (и какой срок) или одного этого мало (и почему).

Что пробуется, по виду совета:

* **площадка** с местом — сначала площадка без новых бортов (распределение
  само ставит на неё не приписанные борта), затем площадка плюс один борт
  каждой годной модели пула, приписанный к ней;
* **борт** — один борт названной модели или, если модель не названа, каждой
  годной модели пула по очереди;
* **комплекты АКБ** — без предела числа вылетов.

Берётся первая правка, при которой задание выполнимо. Предел флота (15
бортов) не превышается: такую правку совет не проверяет. Проверочные расчёты —
без целевых сроков эвристики и без проверки их собственных советов.
"""

from __future__ import annotations

from collections.abc import Callable

from core import assumptions, text
from core.geo.mask import WorkArea
from core.schemas import (
    FleetUnit,
    Mission,
    Recommendation,
    RecommendationKind,
    Scenario,
    Site,
    SiteKind,
    SurveyPlan,
    Vehicle,
)

#: Расчёт сценария конвейером: итог — план или отчёт о невыполнимости.
PlanFn = Callable[[Scenario], Mission]

#: Идентификатор площадки, которую предлагает совет.
ADVISED_SITE_ID = "advised-site"


def _usable_models(scenario: Scenario, survey_plan: SurveyPlan) -> list[str]:
    """Модели пула, годные для этой съёмки, — в порядке пула, без повторов."""
    usable = {params.vehicle_id for params in survey_plan.per_vehicle}
    models: list[str] = []
    for unit in scenario.fleet:
        if unit.vehicle_id in usable and unit.vehicle_id not in models:
            models.append(unit.vehicle_id)
    return models


def _pool_size(scenario: Scenario) -> int:
    return sum(unit.count for unit in scenario.fleet)


def _with_board(scenario: Scenario, vehicle_id: str, site_id: str | None = None) -> Scenario:
    """Сценарий с ещё одним бортом модели — с приписанной площадкой или без."""
    unit = FleetUnit(vehicle_id=vehicle_id, count=1, site_id=site_id)
    return scenario.model_copy(update={"fleet": [*scenario.fleet, unit]})


def _trials(
    item: Recommendation,
    scenario: Scenario,
    work_area: WorkArea,
    survey_plan: SurveyPlan,
    vehicles: dict[str, Vehicle],
) -> list[tuple[str, Scenario]]:
    """Правки сценария, которыми проверяется совет, — с подписью «что добавлено»."""
    room = _pool_size(scenario) < assumptions.MAX_FLEET_VEHICLES
    models = _usable_models(scenario, survey_plan)
    if item.kind is RecommendationKind.ADD_SITE and item.position is not None:
        position = work_area.projector.xy_to_geo(item.position.x_m, item.position.y_m)
        site = Site(
            id=ADVISED_SITE_ID,
            name="Площадка по рекомендации",
            position=position,
            kind=SiteKind.PRIMARY,
        )
        with_site = scenario.model_copy(update={"sites": [*scenario.sites, site]})
        trials = [("с площадкой в предложенном месте", with_site)]
        if room:
            trials += [
                (
                    f"с площадкой в предложенном месте и одним {vehicles[model].name} на ней",
                    _with_board(with_site, model, ADVISED_SITE_ID),
                )
                for model in models
            ]
        return trials
    if item.kind is RecommendationKind.ADD_VEHICLE and room:
        chosen = [item.vehicle_id] if item.vehicle_id in models else models
        return [
            (f"с ещё одним {vehicles[model].name}", _with_board(scenario, model))
            for model in chosen
        ]
    if item.kind is RecommendationKind.ADD_BATTERIES:
        fleet = [unit.model_copy(update={"battery_sets": None}) for unit in scenario.fleet]
        return [("без предела числа вылетов", scenario.model_copy(update={"fleet": fleet}))]
    return []


def _outcome(what: str, mission: Mission) -> str:
    """Итог проверочного расчёта одной фразой."""
    if mission.is_feasible() and mission.metrics is not None:
        return (
            f"Проверено расчётом: {what} задание выполнимо, срок "
            f"{text.duration_text(mission.metrics.makespan_s)}"
        )
    reason = mission.reasons[0].message if mission.reasons else "причина не названа"
    return f"Проверено расчётом: {what} задание всё ещё невыполнимо — {text.lower_first(reason)}"


def verify_recommendations(
    scenario: Scenario,
    work_area: WorkArea,
    survey_plan: SurveyPlan,
    vehicles: dict[str, Vehicle],
    recommendations: list[Recommendation],
    plan: PlanFn,
) -> list[Recommendation]:
    """Проверить советы расчётом и дописать к каждому итог.

    Args:
        scenario: невыполнимый сценарий.
        work_area: его рабочая область — место площадки переводится в WGS84.
        survey_plan: параметры съёмки — какие модели пула годны.
        vehicles: модели пула — для имён.
        recommendations: советы правил (`diagnose`).
        plan: расчёт сценария конвейером.

    Returns:
        Те же советы; проверяемые — с `checked` (True — с правкой задание
        выполнимо) и итогом в конце сообщения.
    """
    result: list[Recommendation] = []
    for item in recommendations:
        trials = _trials(item, scenario, work_area, survey_plan, vehicles)
        if not trials:
            result.append(item)
            continue
        verdict = ""
        feasible = False
        for what, trial in trials:
            mission = plan(trial)
            feasible = mission.is_feasible()
            verdict = _outcome(what, mission)
            if feasible:
                break
        result.append(
            item.model_copy(update={"checked": feasible, "message": f"{item.message}. {verdict}"})
        )
    return result


__all__ = ["ADVISED_SITE_ID", "PlanFn", "verify_recommendations"]
