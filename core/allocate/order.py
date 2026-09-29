"""Порядок облёта подобластей внутри вылета (E6, долг №58).

Эвристика добирает вылет «ближайшей к последней добавленной» подобластью, и
порядок облёта остаётся порядком добора: на `real_msk` перелёты вылетов
самолёта по прямой на 20% длиннее, чем после перестановок
(docs/dev/current_state.md, журнал 2026-09-28). Здесь порядок улучшается как
маршрут коммивояжёра, у которого каждая подобласть — отрезок «вход → выход»
змейки, проходимый в любую сторону (Bähnemann и др., 2021: покрытие как
обобщённая задача коммивояжёра по вариантам входа).

Ходы — 2-opt (разворот куска маршрута; подобласти внутри куска проходятся
задом наперёд, связки внутри куска не меняют длины) и перенос одной
подобласти на лучшее место в лучшем направлении. Оценка хода — прямые между
концами змеек, за O(1). Направления прохода итоговый вылет выбирает сам
(`core.flight.cost.pass_directions`); здесь важен только порядок.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from core import assumptions
from core.flight.cost import CellEnds
from core.schemas import MetricPoint

#: Концы змейки подобласти у борта: (подобласть, модель) → вход и выход.
CellEndsOf = Callable[[str, str], CellEnds]

_Point = tuple[float, float]


def _d(a: _Point, b: _Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def improve_order(
    cell_ids: list[str],
    ends: dict[str, tuple[MetricPoint, MetricPoint]],
    start: MetricPoint,
    end: MetricPoint,
) -> list[str]:
    """Переставить подобласти вылета так, чтобы связки между ними стали короче.

    Детерминирован: ходы перебираются в заданном порядке, берётся первый
    улучшающий, пока улучшения есть.

    Args:
        cell_ids: подобласти в текущем порядке облёта.
        ends: концы змейки каждой подобласти (вход, выход), метры.
        start: площадка взлёта, метры.
        end: площадка посадки, метры.

    Returns:
        Подобласти в новом порядке; тот же список, если лучше не нашлось.
    """
    count = len(cell_ids)
    if count < 2:
        return list(cell_ids)
    # Узлы маршрута: площадка взлёта, подобласти, площадка посадки. У каждого
    # узла — вход и выход; у площадок они совпадают.
    ids: list[str | None] = [None, *cell_ids, None]
    entries: list[_Point] = [(start.x_m, start.y_m)]
    exits: list[_Point] = [(start.x_m, start.y_m)]
    for cell_id in cell_ids:
        entry, exit_ = ends[cell_id]
        entries.append((entry.x_m, entry.y_m))
        exits.append((exit_.x_m, exit_.y_m))
    entries.append((end.x_m, end.y_m))
    exits.append((end.x_m, end.y_m))
    eps = assumptions.GEOMETRY_TOLERANCE_M

    improved = True
    while improved:
        improved = False
        # 2-opt: кусок [i..j] задом наперёд, каждая подобласть в нём — тоже.
        for i in range(1, count + 1):
            for j in range(i, count + 1):
                delta = (
                    _d(exits[i - 1], exits[j])
                    + _d(entries[i], entries[j + 1])
                    - _d(exits[i - 1], entries[i])
                    - _d(exits[j], entries[j + 1])
                )
                if delta < -eps:
                    segment = list(range(i, j + 1))[::-1]
                    ids[i : j + 1] = [ids[k] for k in segment]
                    entries[i : j + 1], exits[i : j + 1] = (
                        [exits[k] for k in segment],
                        [entries[k] for k in segment],
                    )
                    improved = True
        # Перенос: подобласть k — между m и m + 1, в лучшем направлении.
        for k in range(1, count + 1):
            gain = (
                _d(exits[k - 1], entries[k])
                + _d(exits[k], entries[k + 1])
                - _d(exits[k - 1], entries[k + 1])
            )
            best: tuple[float, int, bool] | None = None
            for m in range(0, count + 1):
                if m in (k - 1, k):
                    continue
                base = _d(exits[m], entries[m + 1])
                for flip in (False, True):
                    entry, exit_ = (exits[k], entries[k]) if flip else (entries[k], exits[k])
                    cost = _d(exits[m], entry) + _d(exit_, entries[m + 1]) - base
                    if cost - gain < -eps and (best is None or cost < best[0]):
                        best = (cost, m, flip)
            if best is None:
                continue
            _cost, m, flip = best
            node = (ids[k], exits[k], entries[k]) if flip else (ids[k], entries[k], exits[k])
            del ids[k], entries[k], exits[k]
            at = m + 1 if m < k else m
            ids.insert(at, node[0])
            entries.insert(at, node[1])
            exits.insert(at, node[2])
            improved = True
    return [cell_id for cell_id in ids[1:-1] if cell_id is not None]


def cheapest_insertion(
    cell_ids: list[str],
    cell_id: str,
    ends: dict[str, tuple[MetricPoint, MetricPoint]],
    start: MetricPoint,
    end: MetricPoint,
) -> list[str]:
    """Вставить подобласть туда, где связки удлинятся меньше всего.

    Args:
        cell_ids: подобласти вылета по порядку облёта.
        cell_id: новая подобласть.
        ends: концы змейки каждой подобласти, включая новую, метры.
        start: площадка взлёта, метры.
        end: площадка посадки, метры.

    Returns:
        Новый порядок облёта.
    """
    points: list[tuple[_Point, _Point]] = [((start.x_m, start.y_m), (start.x_m, start.y_m))]
    for item in cell_ids:
        entry, exit_ = ends[item]
        points.append(((entry.x_m, entry.y_m), (exit_.x_m, exit_.y_m)))
    points.append(((end.x_m, end.y_m), (end.x_m, end.y_m)))
    entry, exit_ = ends[cell_id]
    new = ((entry.x_m, entry.y_m), (exit_.x_m, exit_.y_m))
    best: tuple[float, int] | None = None
    for m in range(len(points) - 1):
        base = _d(points[m][1], points[m + 1][0])
        for a, b in (new, new[::-1]):
            cost = _d(points[m][1], a) + _d(b, points[m + 1][0]) - base
            if best is None or cost < best[0] - 1e-9:
                best = (cost, m)
    assert best is not None
    at = best[1]
    return [*cell_ids[:at], cell_id, *cell_ids[at:]]


__all__ = ["CellEndsOf", "cheapest_insertion", "improve_order"]
