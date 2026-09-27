"""Граф видимости (E5, долг №48): ленивый поиск против полного перебора.

Маршрутизатор не строит граф целиком и отбрасывает рёбра, которые не касаются
отодвинутых областей в обоих концах. Проверяется, что это только ускорение:
длина пути зажата между двумя эталонами полного перебора всех пар узлов.

- Снизу — прежний алгоритм: все вершины отодвинутых контуров, видимость по
  настоящей геометрии. Он может срезать угол через полосу отступа, поэтому
  бывает на несколько метров короче.
- Сверху — классический кратчайший путь вокруг отодвинутых областей: рёбра
  между узлами не заходят внутрь них.
"""

from __future__ import annotations

import heapq
import math
import random
import time

import pytest
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

from core import assumptions
from core.transit.routing import _VisibilityGraph

_MARGIN = assumptions.TRANSIT_ROUTE_MARGIN_M
_TOLERANCE = assumptions.BOUNDARY_TOLERANCE_M


def _ring_points(geometry) -> list[tuple[float, float]]:
    polygons = list(getattr(geometry, "geoms", [geometry]))
    points: list[tuple[float, float]] = []
    for polygon in polygons:
        for ring in [polygon.exterior, *polygon.interiors]:
            points.extend((x, y) for x, y in list(ring.coords)[:-1])
    return points


def _shortest(nodes, start, end, visible_nodes, visible_ends) -> float:
    """Дейкстра по полному графу видимости; inf — пути нет."""
    count = len(nodes)
    source, target = count, count + 1
    points = [*nodes, start, end]
    best = {source: 0.0}
    queue = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if node == target:
            return distance
        if distance > best.get(node, math.inf):
            continue
        for other in range(count + 2):
            if other in (node, source):
                continue
            pair = {node, other}
            if pair & {source, target}:
                if pair == {source, target}:
                    ok = visible_ends(start, end)
                else:
                    ok = visible_ends(points[node], points[other])
            else:
                ok = visible_nodes(points[node], points[other])
            if not ok:
                continue
            candidate = distance + math.dist(points[node], points[other])
            if candidate < best.get(other, math.inf):
                best[other] = candidate
                heapq.heappush(queue, (candidate, other))
    return math.inf


def _references(blocked, checked, start, end) -> tuple[float, float]:
    """Длины пути двух эталонов полного перебора: (прежний, вокруг отступа).

    Классический обход не заходит внутрь отодвинутых областей нигде, кроме
    ребра из конца пути, который сам лежит в полосе отступа: из неё иначе не
    выйти.
    """
    outset = blocked.buffer(_MARGIN, join_style="mitre")
    checked_prep = prep(checked)
    outset_core = prep(outset.buffer(-assumptions.GEOMETRY_TOLERANCE_M))
    nodes = [p for p in _ring_points(outset) if not checked_prep.intersects(Point(p))]
    in_band = {point for point in (start, end) if outset.intersects(Point(point))}

    def through_checked(a, b) -> bool:
        return not checked_prep.intersects(LineString([a, b]))

    def around_outset(a, b) -> bool:
        return not outset_core.intersects(LineString([a, b]))

    def classic_ends(a, b) -> bool:
        return through_checked(a, b) if {a, b} & in_band else around_outset(a, b)

    old = _shortest(nodes, start, end, through_checked, through_checked)
    classic = _shortest(nodes, start, end, around_outset, classic_ends)
    return old, classic


def _length(path) -> float:
    return sum(math.dist(a, b) for a, b in zip(path, path[1:], strict=False))


def _obstacles(seed: int) -> list[Polygon]:
    """Случайные препятствия: круги, прямоугольники и слипшиеся пары."""
    rng = random.Random(seed)
    shapes: list[Polygon] = []
    for _ in range(7):
        x, y = rng.uniform(300, 2700), rng.uniform(300, 2700)
        kind = rng.choice(["circle", "box", "pair"])
        if kind == "circle":
            shapes.append(Point(x, y).buffer(rng.uniform(40, 200), quad_segs=4))
        elif kind == "box":
            w, h = rng.uniform(50, 600), rng.uniform(50, 600)
            shapes.append(box(x - w / 2, y - h / 2, x + w / 2, y + h / 2))
        else:
            shapes.append(Point(x, y).buffer(120, quad_segs=4))
            shapes.append(Point(x + 150, y + 60).buffer(120, quad_segs=4))
    return shapes


@pytest.mark.parametrize("seed", range(6))
def test_lazy_search_is_bounded_by_full_enumeration(seed: int) -> None:
    """Путь не короче прежнего перебора и не длиннее обхода по отступу."""
    shapes = _obstacles(seed)
    blocked = unary_union(shapes)
    checked = blocked.buffer(-_TOLERANCE)
    graph = _VisibilityGraph(blocked, checked, None)
    rng = random.Random(100 + seed)
    outset = blocked.buffer(_MARGIN, join_style="mitre")
    compared = 0
    while compared < 6:
        start = (rng.uniform(0, 3000), rng.uniform(0, 3000))
        end = (rng.uniform(0, 3000), rng.uniform(0, 3000))
        if outset.contains(Point(start)) or outset.contains(Point(end)):
            continue
        path = graph.route(start, end)
        old, classic = _references(blocked, checked, start, end)
        assert not LineString(path).intersects(checked)
        assert old - 1e-6 <= _length(path) <= classic + 1e-6
        compared += 1


def test_route_leaves_the_margin_band_around_its_start() -> None:
    """Конец галса лежит на краю буфера, внутри полосы отступа — обход есть.

    Отбор рёбер по касанию для такой точки неверен: путь выходит из неё к
    узлу не по касательной. Прямая сквозь препятствие здесь — нарушение.
    """
    mast = Point(1000.0, 1000.0).buffer(assumptions.OBSTACLE_BUFFER_M)
    checked = mast.buffer(-_TOLERANCE)
    graph = _VisibilityGraph(mast, checked, None)
    start = (1000.0 - assumptions.OBSTACLE_BUFFER_M, 1000.0)
    end = (1000.0 + assumptions.OBSTACLE_BUFFER_M, 1000.0)
    path = graph.route(start, end)
    old, classic = _references(mast, checked, start, end)
    assert len(path) > 2
    assert not LineString(path).intersects(checked)
    assert old - 1e-6 <= _length(path) <= classic + 1e-6


def test_route_respects_airspace_boundary() -> None:
    """Граница пространства — стена: путь огибает её вогнутый угол изнутри."""
    airspace = Polygon([(0, 0), (3000, 0), (3000, 3000), (1600, 3000), (1600, 800), (0, 800)])
    graph = _VisibilityGraph(Polygon(), Polygon(), airspace)
    start, end = (300.0, 400.0), (2200.0, 2600.0)
    path = graph.route(start, end)
    assert len(path) == 3
    assert airspace.buffer(_TOLERANCE).contains(LineString(path))


def test_router_scales_to_customer_obstacle_count() -> None:
    """Долг №48: 600 мачт на 60 × 60 км — как в данных заказчика.

    Прежний граф строился перебором всех пар узлов: на 547 препятствиях
    заказчика — 246 с на эшелон. Ленивый поиск считает рёбра только у узлов,
    до которых дошёл. Порог времени щедрый: он ловит возврат к перебору, а не
    колебания скорости машины.
    """
    rng = random.Random(7)
    masts = [
        Point(rng.uniform(0, 60_000), rng.uniform(0, 60_000)).buffer(assumptions.OBSTACLE_BUFFER_M)
        for _ in range(600)
    ]
    blocked = unary_union(masts)
    checked = unary_union([mast.buffer(-_TOLERANCE) for mast in masts])
    started = time.perf_counter()
    graph = _VisibilityGraph(blocked, checked, None)
    assert len(graph.nodes) > 10_000
    for _ in range(60):
        start = (rng.uniform(0, 60_000), rng.uniform(0, 60_000))
        end = (rng.uniform(0, 60_000), rng.uniform(0, 60_000))
        path = graph.route(start, end)
        assert not LineString(path).intersects(checked)
    assert time.perf_counter() - started < 30.0
