"""Перелёты в обход запретных зон и препятствий: граф видимости (E5).

Перелёт идёт на эшелоне борта. Препятствием для него служит всё, что
запрещает эту высоту: запретные зоны, чей высотный диапазон её накрывает
(с буфером безопасности), и препятствия, выше которых эшелон не проходит с
запасом. Граница разрешённого воздушного пространства — тоже стена.

Узлы графа — вершины этих областей, отодвинутых ещё на `TRANSIT_ROUTE_MARGIN_M`
со скошенными углами; рёбра — отрезки, не задевающие запрещённое. Кратчайший
путь — A* с расстоянием по прямой до цели как оценкой.

Граф не строится целиком (долг №48: на реальной области заказчика перебор
всех пар узлов на одном эшелоне шёл 246 с). Рёбра узла считаются, когда поиск
до него дошёл, и запоминаются. В проверку видимости идут не все пары, а только
те, что могут лежать на кратчайшем пути вокруг отодвинутых областей:

- узел — выпуклая вершина: в вогнутой кратчайший путь не поворачивает;
- ребро касается области в обоих концах — оба соседа узла по контуру лежат
  по одну сторону от ребра. Ребро, которое заходит внутрь угла, путь не
  сокращает: обход по касательной короче.

Оба отбора — арифметика над массивами координат; дорогая геометрическая
проверка достаётся единицам процентов пар. Видимость каждого оставшегося ребра
проверяется по настоящей геометрии, как и раньше: отбор отсекает только
заведомо лишние рёбра и на безопасность не влияет.

Упрощения (TODO(E5)): временные зоны обходятся всегда, как постоянные, —
это безопасно, но иногда длиннее, чем подождать окончания окна; путь не
сглаживается под радиус разворота самолёта. Если обхода нет (точка внутри
запрещённой области), возвращается прямая — валидатор объявит нарушение.
"""

from __future__ import annotations

import heapq
import math

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from core import assumptions
from core.geo.mask import WorkArea
from core.geo.project import polygons_of
from core.schemas import MetricLineString, MetricPoint

_Point = tuple[float, float]


def _convex_corners(geometry: BaseGeometry, blocked_inside: bool) -> np.ndarray:
    """Выпуклые, если смотреть со стороны запрещённого, вершины контуров.

    Контуры ориентируются так, что внутренность полигона слева по ходу
    обхода. Тогда поворот влево — выпуклый угол полигона. Для запрещённых
    областей нужны выпуклые углы самих областей, для границы пространства
    — выпуклые углы того, что снаружи, то есть повороты вправо.

    Args:
        geometry: полигон или мультиполигон, метры.
        blocked_inside: True — запрещено внутри (зоны, препятствия),
            False — снаружи (граница пространства).

    Returns:
        Массив (N, 3, 2): вершина, предыдущая и следующая по контуру, метры.
    """
    sign = 1.0 if blocked_inside else -1.0
    corners: list[np.ndarray] = []
    for polygon in polygons_of(geometry):
        oriented = orient(polygon, 1.0)
        for ring in [oriented.exterior, *oriented.interiors]:
            coords = np.asarray(ring.coords, dtype=float)[:-1, :2]
            if len(coords) < 3:
                continue
            previous = np.roll(coords, 1, axis=0)
            following = np.roll(coords, -1, axis=0)
            incoming = coords - previous
            outgoing = following - coords
            turn = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
            keep = sign * turn > 0.0
            corners.append(np.stack([coords[keep], previous[keep], following[keep]], axis=1))
    return np.concatenate(corners) if corners else np.empty((0, 3, 2))


def _side_offsets(origin: np.ndarray, towards: np.ndarray, probe: np.ndarray) -> np.ndarray:
    """Отступ точек `probe` от прямых `origin → towards`, метры, со знаком.

    Все аргументы — массивы (N, 2) или совместимые по размеру. Для
    вырожденной прямой (концы совпали) — NaN.
    """
    direction = towards - origin
    offset = probe - origin
    cross = direction[..., 0] * offset[..., 1] - direction[..., 1] * offset[..., 0]
    length = np.hypot(direction[..., 0], direction[..., 1])
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(length > 0.0, cross / length, np.nan)


def _touches_at(
    corner: np.ndarray, previous: np.ndarray, following: np.ndarray, other: np.ndarray
) -> np.ndarray:
    """Касается ли прямая `corner — other` контура в вершине `corner`.

    Касается — если оба соседа вершины по контуру лежат по одну сторону от
    прямой или на ней (в пределах `GEOMETRY_TOLERANCE_M`).
    """
    tolerance = assumptions.GEOMETRY_TOLERANCE_M
    before = _side_offsets(corner, other, previous)
    after = _side_offsets(corner, other, following)
    on_line = (np.abs(before) <= tolerance) | (np.abs(after) <= tolerance)
    return (before * after >= 0.0) | on_line


class _VisibilityGraph:
    """Граф видимости для одного набора мешающих областей.

    `blocked` — запрещённые области с буфером, по ним ставятся узлы;
    `checked` — они же за вычетом допуска касания, по ним проверяется
    видимость; `airspace` — граница разрешённого пространства или None.
    """

    def __init__(
        self, blocked: BaseGeometry, checked: BaseGeometry, airspace: BaseGeometry | None
    ) -> None:
        self._checked = checked if not checked.is_empty else None
        self._airspace = (
            airspace.buffer(assumptions.BOUNDARY_TOLERANCE_M) if airspace is not None else None
        )
        for shape in (self._checked, self._airspace):
            if shape is not None:
                shapely.prepare(shape)

        margin = assumptions.TRANSIT_ROUTE_MARGIN_M
        parts = []
        # Отодвинутые области: по их контурам стоят узлы. Точка внутри них —
        # в полосе отступа, для неё отбор рёбер по касанию неверен.
        self._outset: BaseGeometry | None = None
        self._inset: BaseGeometry | None = None
        if not blocked.is_empty:
            self._outset = blocked.buffer(margin, join_style="mitre")
            shapely.prepare(self._outset)
            parts.append(_convex_corners(self._outset, True))
        if airspace is not None:
            self._inset = airspace.buffer(-margin, join_style="mitre")
            shapely.prepare(self._inset)
            parts.append(_convex_corners(self._inset, False))
        corners = np.concatenate(parts) if parts else np.empty((0, 3, 2))
        if len(corners):
            free = self._free(shapely.points(corners[:, 0]))
            corners = corners[free]
        self.nodes: np.ndarray = corners[:, 0] if len(corners) else np.empty((0, 2))
        self._previous = corners[:, 1] if len(corners) else np.empty((0, 2))
        self._following = corners[:, 2] if len(corners) else np.empty((0, 2))
        self._neighbours: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def _free(self, shapes: np.ndarray) -> np.ndarray:
        """Какие из геометрий не задевают запрещённое и не выходят за пространство."""
        free = np.ones(len(shapes), dtype=bool)
        if self._checked is not None:
            free &= ~shapely.intersects(self._checked, shapes)
        if self._airspace is not None:
            free &= shapely.contains(self._airspace, shapes)
        return free

    def visible(self, a: _Point, b: _Point) -> bool:
        """Виден ли отрезок a–b: не задевает запрещённое, внутри пространства."""
        return bool(self._free(np.array([LineString([a, b])], dtype=object))[0])

    def _visible_from(self, origin: np.ndarray, candidates: np.ndarray) -> np.ndarray:
        """Какие узлы из `candidates` видны из точки `origin`."""
        if not len(candidates):
            return candidates
        ends = self.nodes[candidates]
        lines = shapely.linestrings(np.stack([np.broadcast_to(origin, ends.shape), ends], axis=1))
        return candidates[self._free(lines)]

    def _in_margin(self, point: np.ndarray) -> bool:
        """Лежит ли точка в полосе отступа вокруг запрещённого."""
        here = shapely.points(point)
        if self._outset is not None and shapely.intersects(self._outset, here):
            return True
        return self._inset is not None and not shapely.contains(self._inset, here)

    def _links(self, point: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Узлы, в которые можно пойти из внешней точки, и длины рёбер, метры.

        Касание нужно только в узле: сама точка — начало или конец пути. Из
        точки в полосе отступа (конец галса у края буфера) путь выходит к
        узлу не по касательной — тогда проверяются все узлы.
        """
        touching = np.any(self.nodes != point, axis=1)
        if not self._in_margin(point):
            touching &= _touches_at(self.nodes, self._previous, self._following, point)
        visible = self._visible_from(point, np.flatnonzero(touching))
        return visible, np.hypot(*(self.nodes[visible] - point).T)

    def _neighbours_of(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        """Соседи узла и длины рёбер, метры; считаются один раз."""
        cached = self._neighbours.get(index)
        if cached is not None:
            return cached
        corner = self.nodes[index]
        # Ребро касается контура в том узле, куда ведёт...
        touching = _touches_at(self.nodes, self._previous, self._following, corner)
        # ...и в том, откуда выходит.
        touching &= _touches_at(corner, self._previous[index], self._following[index], self.nodes)
        touching[index] = False
        visible = self._visible_from(corner, np.flatnonzero(touching))
        result = (visible, np.hypot(*(self.nodes[visible] - corner).T))
        self._neighbours[index] = result
        return result

    def route(self, start: _Point, end: _Point) -> list[_Point]:
        """Кратчайший путь по графу; прямая, если обхода нет или он не нужен."""
        if not len(self.nodes) or self.visible(start, end):
            return [start, end]
        source = np.asarray(start, dtype=float)
        target = np.asarray(end, dtype=float)
        first, first_lengths = self._links(source)
        last, _ = self._links(target)
        if not len(first) or not len(last):
            return [start, end]

        # Оценка остатка — расстояние по прямой до цели. Из узла, который видит
        # цель, остаток равен оценке, поэтому первый такой узел, снятый с
        # очереди, замыкает кратчайший путь.
        remaining = np.hypot(*(self.nodes - target).T)
        sees_target = np.zeros(len(self.nodes), dtype=bool)
        sees_target[last] = True
        best = np.full(len(self.nodes), math.inf)
        previous = np.full(len(self.nodes), -1, dtype=np.int64)
        closed = np.zeros(len(self.nodes), dtype=bool)
        queue: list[tuple[float, int]] = []
        for node, length in zip(first.tolist(), first_lengths.tolist(), strict=True):
            best[node] = length
            heapq.heappush(queue, (length + remaining[node], node))

        while queue:
            _estimate, node = heapq.heappop(queue)
            if closed[node]:
                continue
            closed[node] = True
            if sees_target[node]:
                path = [end]
                while node >= 0:
                    path.append((float(self.nodes[node, 0]), float(self.nodes[node, 1])))
                    node = int(previous[node])
                path.append(start)
                path.reverse()
                return path
            neighbours, lengths = self._neighbours_of(node)
            candidate = best[node] + lengths
            better = candidate < best[neighbours]
            for neighbour, distance in zip(
                neighbours[better].tolist(), candidate[better].tolist(), strict=True
            ):
                if closed[neighbour]:
                    continue
                best[neighbour] = distance
                previous[neighbour] = node
                heapq.heappush(queue, (distance + remaining[neighbour], neighbour))
        return [start, end]


class VisibilityRouter:
    """Маршрутизатор перелётов с обходом по графу видимости."""

    def __init__(self, work_area: WorkArea) -> None:
        self._work_area = work_area
        self._graphs: dict[tuple[str, ...], _VisibilityGraph] = {}
        self._by_altitude: dict[float, tuple[tuple[str, ...], _VisibilityGraph]] = {}
        self._routes: dict[tuple, MetricLineString] = {}

    def _graph_for(self, altitude_m: float) -> tuple[tuple[str, ...], _VisibilityGraph]:
        cached = self._by_altitude.get(altitude_m)
        if cached is None:
            cached = self._by_altitude[altitude_m] = self._build_graph_for(altitude_m)
        return cached

    def _build_graph_for(self, altitude_m: float) -> tuple[tuple[str, ...], _VisibilityGraph]:
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
