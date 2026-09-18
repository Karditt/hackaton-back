"""Перелёты в обход запретных зон и препятствий: граф видимости (E5).

Перелёт идёт на эшелоне борта. Препятствием для него служит всё, что
запрещает эту высоту: запретные зоны, чей высотный диапазон её накрывает
(с буфером безопасности), и препятствия, выше которых эшелон не проходит с
запасом. Граница разрешённого воздушного пространства — тоже стена.

Узлы графа — вершины этих областей, отодвинутых ещё на `TRANSIT_ROUTE_MARGIN_M`
со скошенными углами; рёбра — отрезки, не задевающие запрещённое. Кратчайший
путь — Дейкстра. Граф строится один раз на набор мешающих объектов и
переиспользуется всеми вылетами на эшелонах с тем же набором.

Упрощения (TODO(E5)): временные зоны обходятся всегда, как постоянные, —
это безопасно, но иногда длиннее, чем подождать окончания окна; путь не
сглаживается под радиус разворота самолёта. Если обхода нет (точка внутри
запрещённой области), возвращается прямая — валидатор объявит нарушение.
"""

from __future__ import annotations

import heapq
import math

from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import prep

from core import assumptions
from core.geo.mask import WorkArea
from core.schemas import MetricLineString, MetricPoint

_Point = tuple[float, float]


def _ring_vertices(geometry: BaseGeometry) -> list[_Point]:
    """Вершины всех колец полигональной геометрии без замыкающих повторов."""
    polygons: list[Polygon] = []
    if isinstance(geometry, Polygon):
        polygons = [geometry]
    elif isinstance(geometry, MultiPolygon):
        polygons = list(geometry.geoms)
    vertices: list[_Point] = []
    for polygon in polygons:
        for ring in [polygon.exterior, *polygon.interiors]:
            vertices.extend((x, y) for x, y in list(ring.coords)[:-1])
    return vertices


class _VisibilityGraph:
    """Граф видимости для одного набора мешающих областей.

    `blocked` — запрещённые области с буфером, по ним ставятся узлы;
    `checked` — они же за вычетом допуска касания, по ним проверяется
    видимость; `airspace` — граница разрешённого пространства или None.
    """

    def __init__(
        self, blocked: BaseGeometry, checked: BaseGeometry, airspace: BaseGeometry | None
    ) -> None:
        self._blocked = prep(checked) if not checked.is_empty else None
        self._airspace = (
            prep(airspace.buffer(assumptions.BOUNDARY_TOLERANCE_M))
            if airspace is not None
            else None
        )
        margin = assumptions.TRANSIT_ROUTE_MARGIN_M
        candidates: list[_Point] = []
        if not blocked.is_empty:
            candidates.extend(_ring_vertices(blocked.buffer(margin, join_style="mitre")))
        if airspace is not None:
            candidates.extend(_ring_vertices(airspace.buffer(-margin, join_style="mitre")))
        self.nodes = [point for point in candidates if self._free(point)]
        self._edges: list[list[tuple[int, float]]] = [[] for _ in self.nodes]
        for i, a in enumerate(self.nodes):
            for j in range(i + 1, len(self.nodes)):
                b = self.nodes[j]
                if self.visible(a, b):
                    length = math.dist(a, b)
                    self._edges[i].append((j, length))
                    self._edges[j].append((i, length))

    def _free(self, point: _Point) -> bool:
        return self.visible_shape(Point(point))

    def visible_shape(self, shape: BaseGeometry) -> bool:
        """Не задевает ли геометрия запрещённое и не выходит ли за пространство."""
        if self._blocked is not None and self._blocked.intersects(shape):
            return False
        return self._airspace is None or self._airspace.contains(shape)

    def visible(self, a: _Point, b: _Point) -> bool:
        """Виден ли отрезок a–b: не задевает запрещённое, внутри пространства."""
        return self.visible_shape(LineString([a, b]))

    def route(self, start: _Point, end: _Point) -> list[_Point]:
        """Кратчайший путь по графу; прямая, если обхода нет или он не нужен."""
        if self.visible(start, end) or not self.nodes:
            return [start, end]
        count = len(self.nodes)
        source, target = count, count + 1
        links_from_start = [
            (i, math.dist(start, p)) for i, p in enumerate(self.nodes) if self.visible(start, p)
        ]
        links_to_end = {
            i: math.dist(p, end) for i, p in enumerate(self.nodes) if self.visible(p, end)
        }
        if not links_from_start or not links_to_end:
            return [start, end]

        best = {source: 0.0}
        previous: dict[int, int] = {}
        queue: list[tuple[float, int]] = [(0.0, source)]
        while queue:
            distance, node = heapq.heappop(queue)
            if node == target:
                break
            if distance > best.get(node, math.inf):
                continue
            neighbours = links_from_start if node == source else self._edges[node]
            for neighbour, length in neighbours:
                candidate = distance + length
                if candidate < best.get(neighbour, math.inf):
                    best[neighbour] = candidate
                    previous[neighbour] = node
                    heapq.heappush(queue, (candidate, neighbour))
            if node in links_to_end:
                candidate = distance + links_to_end[node]
                if candidate < best.get(target, math.inf):
                    best[target] = candidate
                    previous[target] = node
                    heapq.heappush(queue, (candidate, target))

        if target not in previous:
            return [start, end]
        path = [end]
        node = previous[target]
        while node != source:
            path.append(self.nodes[node])
            node = previous[node]
        path.append(start)
        path.reverse()
        return path


class VisibilityRouter:
    """Маршрутизатор перелётов с обходом по графу видимости."""

    def __init__(self, work_area: WorkArea) -> None:
        self._work_area = work_area
        self._graphs: dict[tuple[str, ...], _VisibilityGraph] = {}
        self._routes: dict[tuple, MetricLineString] = {}

    def _graph_for(self, altitude_m: float) -> tuple[tuple[str, ...], _VisibilityGraph]:
        zones = [
            zone for zone in self._work_area.zones if zone.blocks_altitude(altitude_m, altitude_m)
        ]
        obstacles = [item for item in self._work_area.obstacles if item.blocks_altitude(altitude_m)]
        key = tuple(
            sorted([*(f"z:{zone.id}" for zone in zones), *(f"o:{item.id}" for item in obstacles)])
        )
        if key not in self._graphs:
            blocked = unary_union(
                [zone.buffered for zone in zones] + [item.buffered for item in obstacles]
            )
            checked = unary_union(
                [zone.inner for zone in zones] + [item.inner for item in obstacles]
            )
            self._graphs[key] = _VisibilityGraph(
                blocked if not blocked.is_empty else Polygon(),
                checked if not checked.is_empty else Polygon(),
                self._work_area.airspace,
            )
        return key, self._graphs[key]

    def __call__(self, start: MetricPoint, end: MetricPoint, altitude_m: float) -> MetricLineString:
        """Проложить перелёт на заданной высоте.

        Args:
            start: точка начала, метры рабочей проекции.
            end: точка конца, метры рабочей проекции.
            altitude_m: высота перелёта, м AGL — от неё зависит, что мешает.

        Returns:
            Ломаная обхода; прямая, если обход не нужен или невозможен.
        """
        key, graph = self._graph_for(altitude_m)
        cache_key = (
            key,
            round(start.x_m, 3),
            round(start.y_m, 3),
            round(end.x_m, 3),
            round(end.y_m, 3),
        )
        if cache_key not in self._routes:
            path = graph.route((start.x_m, start.y_m), (end.x_m, end.y_m))
            self._routes[cache_key] = MetricLineString(
                points=[MetricPoint(x_m=x, y_m=y) for x, y in path]
            )
        return self._routes[cache_key]


def make_router(work_area: WorkArea) -> VisibilityRouter:
    """Создать маршрутизатор перелётов для рабочей области."""
    return VisibilityRouter(work_area)


__all__ = ["VisibilityRouter", "make_router"]
