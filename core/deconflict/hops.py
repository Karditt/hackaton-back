"""Перелёты между подобластями вылета на высоте съёмки (E14, долг №57).

Настоящая область заказчика — сотни островков, разделённых землёй вне
задания. Пока борт переходил с островка на островок только через свой эшелон,
на `real_msk` у самолёта выходило по 21 подъёму и спуску за вылет — 7,5 ч
набора и снижения на пять бортов (docs/dev/current_state.md, журнал 2026-09-28).

Эшелон нужен, чтобы не встретить на высоте съёмки другой борт, который там
работает (инвариант 9). Над землёй вне области съёмки никто не работает —
значит перелёт на высоте съёмки безопасен, если он держится дальше
`clearance_m` от чужих подобластей: зона манёвра самолёта, который может там
снимать на близкой высоте, плюс минимум разделения. Тогда перелёт не заходит
ни в чужую зону манёвра, ни ближе минимума к чужим галсам, и проверки
расписания и краш-теста, которые сравнивают траектории и зоны манёвра, ничего
не упускают. Чьи подобласти — неизвестно до конца распределения, поэтому чужие
— все, кроме подобластей этого вылета.

Перелёт прокладывается маршрутизатором на высоте съёмки — в обход зон и
препятствий, которые мешают этой высоте, — и ещё раз сверяется с ними: если
обхода не нашлось, маршрутизатор отдаёт прямую, и тогда остаётся эшелон.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep
from shapely.strtree import STRtree

from core import assumptions
from core.flight.cost import SurveyHopCheck
from core.geo.mask import WorkArea
from core.geo.project import polygon_to_shapely
from core.schemas import Cell, MetricLineString, MetricPoint, SurveyPlan, Vehicle


class SurveyHopPolicy:
    """Когда перелёт между подобластями вылета можно пройти на высоте съёмки."""

    def __init__(
        self,
        cells: list[Cell],
        survey_plan: SurveyPlan,
        vehicles: dict[str, Vehicle],
        work_area: WorkArea,
    ) -> None:
        """Подготовить индекс подобластей и запреты по высотам съёмки.

        Args:
            cells: все подобласти декомпозиции, метры.
            survey_plan: параметры съёмки пригодных бортов.
            vehicles: каталог бортов.
            work_area: рабочая область (зоны, препятствия, пространство).
        """
        self._ids = [cell.id for cell in cells]
        self._tree = STRtree([polygon_to_shapely(cell.polygon) for cell in cells])
        self._clearance: dict[str, float] = {}
        self._blocked: dict[str, PreparedGeometry | None] = {}
        self._airspace = (
            prep(work_area.airspace.buffer(assumptions.BOUNDARY_TOLERANCE_M))
            if work_area.airspace is not None
            else None
        )
        for params in survey_plan.per_vehicle:
            altitude_m = params.altitude_agl_m
            peers = [
                vehicles[other.vehicle_id]
                for other in survey_plan.per_vehicle
                if abs(other.altitude_agl_m - altitude_m) < assumptions.VERTICAL_SEPARATION_M
            ]
            reach_m = max(
                (
                    assumptions.MANEUVER_ZONE_RADIUS_FACTOR * peer.turn_radius_m
                    for peer in peers
                    if peer.is_fixed_wing()
                ),
                default=0.0,
            )
            separation_m = max(
                assumptions.separation_minimum_m(peer.is_fixed_wing()) for peer in peers
            )
            self._clearance[params.vehicle_id] = (
                reach_m + separation_m + assumptions.BOUNDARY_TOLERANCE_M
            )
            hazard = work_area.zones_blocking(
                altitude_m, altitude_m, permanent_only=False, inner=True
            ).union(work_area.obstacles_blocking(altitude_m, inner=True))
            self._blocked[params.vehicle_id] = prep(hazard) if not hazard.is_empty else None

    def clearance_m(self, vehicle_id: str) -> float:
        """Отступ перелёта на высоте съёмки от чужих подобластей, метры."""
        return self._clearance[vehicle_id]

    def check(self, vehicle_id: str, own_ids: set[str], own_area: BaseGeometry) -> SurveyHopCheck:
        """Правило для одного вылета.

        Args:
            vehicle_id: модель борта.
            own_ids: подобласти вылета.
            own_area: их объединение, метры.

        Returns:
            Функция «можно ли этот перелёт пройти на высоте съёмки».
        """
        clearance_m = self._clearance.get(vehicle_id)
        blocked = self._blocked.get(vehicle_id)
        inside = own_area.buffer(assumptions.BOUNDARY_TOLERANCE_M)

        def allowed(line: MetricLineString) -> bool:
            if clearance_m is None:
                return False
            path = LineString([(point.x_m, point.y_m) for point in line.points])
            if blocked is not None and blocked.intersects(path):
                return False
            if self._airspace is not None and not self._airspace.contains(path):
                return False
            outside = path.difference(inside)
            if outside.is_empty:
                return True
            near = self._tree.query(outside, predicate="dwithin", distance=clearance_m)
            return all(self._ids[index] in own_ids for index in near)

        return allowed


class SlopeClearance:
    """Свободен ли наклонный кусок перелёта во всём своём диапазоне высот (долг №56).

    Маршрутизатор прокладывает перелёт на эшелоне и обходит только то, что
    мешает эшелону. Самолёт, набирающий высоту на ходу, идёт по той же ломаной
    ниже — и там могут мешать препятствия пониже и зоны с другими высотами.
    Кусок годится, если не задевает зон, запрещающих хоть одну высоту его
    диапазона (постоянных и временных — окна здесь не смотрятся), и
    препятствий, опасных на нижней его высоте, и не выходит за пространство.
    Та же мера, что у краш-теста (`core.validate.checks`), только заранее.
    """

    def __init__(self, work_area: WorkArea) -> None:
        """Args:
        work_area: рабочая область (зоны, препятствия, пространство).
        """
        self._work_area = work_area
        self._cache: dict[tuple[float, float], PreparedGeometry | None] = {}
        self._airspace = (
            prep(work_area.airspace.buffer(assumptions.BOUNDARY_TOLERANCE_M))
            if work_area.airspace is not None
            else None
        )

    def _blocked(self, low_m: float, high_m: float) -> PreparedGeometry | None:
        # Кеш по диапазону, расширенному до десятых метра наружу: запрет на
        # расширенном диапазоне не меньше, чем на настоящем.
        low = math.floor(low_m * 10.0) / 10.0
        high = math.ceil(high_m * 10.0) / 10.0
        if (low, high) not in self._cache:
            hazard = self._work_area.zones_blocking(
                low, high, permanent_only=False, inner=True
            ).union(self._work_area.obstacles_blocking(low, inner=True))
            self._cache[(low, high)] = prep(hazard) if not hazard.is_empty else None
        return self._cache[(low, high)]

    def __call__(self, points: list[MetricPoint], low_m: float, high_m: float) -> bool:
        """Свободен ли кусок ломаной (метры) на высотах [low_m, high_m], м AGL."""
        coords = [(point.x_m, point.y_m) for point in points]
        path = LineString(coords) if len(set(coords)) > 1 else Point(coords[0])
        blocked = self._blocked(low_m, high_m)
        if blocked is not None and blocked.intersects(path):
            return False
        return self._airspace is None or self._airspace.contains(path)


__all__ = ["SlopeClearance", "SurveyHopPolicy"]
