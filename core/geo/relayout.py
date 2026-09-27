"""Новая раскладка области съёмки из островков настоящей области (E0, E12).

Зачем. Настоящая область заказчика одна (`data/samples/survey_area_msk.kml`),
и алгоритм, настроенный только на неё, легко подогнать под её случайности:
где лежат крупные куски, как далеко они от площадок, какие зоны рядом.
Нужны ещё сценарии того же рода — и с ними отложенная проверка: настраиваем
на одних, итог показываем на других.

Что сохраняется, что меняется. Островки заказчика — формы, размеры,
отверстия — остаются как есть: их статистику не выдумать. Соседние островки
(ближе `link_m` друг к другу) собираются в группу и переносятся целиком —
расстояния внутри группы, от которых зависит, какие куски снимаются одним
вылетом, остаются настоящими. Меняется общая картина: каждая группа встаёт
на случайное место в новом прямоугольнике и поворачивается на случайный
угол. Группы не подходят друг к другу ближе `link_m`, поэтому при том же
пороге группы в новой раскладке те же, что в исходной.

Всё считается в метрах (инвариант 1): куски переводятся в проекцию исходной
области, переносятся в проекцию нового места и только на выходе — в WGS84.
Обе проекции конформные, форма и площадь куска сохраняются с точностью до
масштаба проекций — доли процента.

Раскладка детерминирована зерном: одно зерно — один и тот же файл.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon

from core.errors import GeometryError
from core.geo.crs import pick_working_crs
from core.geo.project import Projector, polygons_of
from core.schemas import GeoMultiPolygon, GeoPoint, GeoPolygon

#: Порог соседства островков, метры. Измерено на области заказчика: при 500 м
#: 549 кусков дают 158 групп, самая большая — 64 куска на 18 км; при 1000 м
#: половина области слипается в одну группу на 35 км и переносить нечего,
#: при 200 м группы дробятся до одиночек (157 из 253).
DEFAULT_LINK_M = 500.0

#: Сколько раз пытаться поставить группу, прежде чем признать, что места нет.
DEFAULT_ATTEMPTS = 5000


@dataclass(frozen=True)
class RelayoutReport:
    """Что сделала раскладка."""

    pieces: int
    groups: int
    area_km2: float
    width_m: float
    height_m: float
    attempts: int
    """Всего попыток поставить группы; сверх числа групп — перестановки из-за тесноты."""


def _groups(parts: list[Polygon], link_m: float) -> list[list[int]]:
    """Группы кусков, связанных соседством ближе `link_m`, метры."""
    tree = shapely.STRtree(parts)
    left, right = tree.query(parts, predicate="dwithin", distance=link_m)
    parent = list(range(len(parts)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for a, b in zip(left.tolist(), right.tolist(), strict=True):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for index in range(len(parts)):
        groups.setdefault(root(index), []).append(index)
    return list(groups.values())


def _polygon_to_geo(projector: Projector, polygon: Polygon) -> GeoPolygon:
    """Полигон в метрах → полигон контракта в WGS84."""
    outer = [
        GeoPoint(lon=lon, lat=lat) for lon, lat in projector.coords_to_geo(polygon.exterior.coords)
    ]
    holes = [
        [GeoPoint(lon=lon, lat=lat) for lon, lat in projector.coords_to_geo(ring.coords)]
        for ring in polygon.interiors
    ]
    return GeoPolygon(outer=outer, holes=holes)


def relayout_area(
    area: GeoMultiPolygon,
    center: GeoPoint,
    width_m: float,
    height_m: float,
    seed: int,
    link_m: float = DEFAULT_LINK_M,
    attempts: int = DEFAULT_ATTEMPTS,
) -> tuple[GeoMultiPolygon, RelayoutReport]:
    """Разложить островки области по новому прямоугольнику.

    Args:
        area: исходная область съёмки, WGS84.
        center: центр нового прямоугольника, WGS84.
        width_m: ширина прямоугольника (восток — запад), метры.
        height_m: высота прямоугольника (север — юг), метры.
        seed: зерно раскладки.
        link_m: порог соседства островков, метры.
        attempts: предел попыток поставить одну группу.

    Returns:
        Пара «новая область в WGS84, отчёт».

    Raises:
        GeometryError: группа больше прямоугольника или для неё не нашлось
            места — прямоугольник слишком тесен.
    """
    source = Projector(pick_working_crs([p for poly in area.polygons for p in poly.outer])[0])
    parts = polygons_of(source.multipolygon_to_metric(area, "Исходная область"))
    target = Projector(pick_working_crs([center])[0])
    anchor = target.to_metric(center)
    left_x, right_x = anchor.x_m - width_m / 2.0, anchor.x_m + width_m / 2.0
    bottom_y, top_y = anchor.y_m - height_m / 2.0, anchor.y_m + height_m / 2.0

    groups = _groups(parts, link_m)
    # Крупные группы — первыми: им труднее найти место.
    groups.sort(key=lambda group: (-sum(parts[i].area for i in group), group[0]))

    rng = random.Random(seed)
    placed = np.empty(0, dtype=object)
    placed_bounds = np.empty((0, 4))
    tries = 0
    for group in groups:
        members = [parts[index] for index in group]
        min_x, min_y, max_x, max_y = shapely.total_bounds(members)
        pivot = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
        for _ in range(attempts):
            tries += 1
            angle = rng.uniform(0.0, 360.0)
            turned = [affinity.rotate(piece, angle, origin=pivot) for piece in members]
            g_min_x, g_min_y, g_max_x, g_max_y = shapely.total_bounds(turned)
            room_x = (right_x - left_x) - (g_max_x - g_min_x)
            room_y = (top_y - bottom_y) - (g_max_y - g_min_y)
            if room_x < 0.0 or room_y < 0.0:
                continue
            dx = left_x + rng.uniform(0.0, room_x) - g_min_x
            dy = bottom_y + rng.uniform(0.0, room_y) - g_min_y
            moved = np.array([affinity.translate(piece, dx, dy) for piece in turned], dtype=object)
            if len(placed) and _crowded(moved, placed, placed_bounds, link_m):
                continue
            placed = np.concatenate([placed, moved])
            placed_bounds = np.vstack([placed_bounds, shapely.bounds(moved)])
            break
        else:
            raise GeometryError(
                f"Группе из {len(group)} островков не нашлось места за {attempts} попыток: "
                f"прямоугольник {width_m / 1000:.0f} × {height_m / 1000:.0f} км тесен"
            )

    result = GeoMultiPolygon(polygons=[_polygon_to_geo(target, piece) for piece in placed])
    report = RelayoutReport(
        pieces=len(placed),
        groups=len(groups),
        area_km2=sum(piece.area for piece in placed) / 1.0e6,
        width_m=width_m,
        height_m=height_m,
        attempts=tries,
    )
    return result, report


def _crowded(
    moved: np.ndarray, placed: np.ndarray, placed_bounds: np.ndarray, link_m: float
) -> bool:
    """Подходит ли группа к уже поставленным кускам ближе `link_m`, метры."""
    for piece in moved:
        min_x, min_y, max_x, max_y = piece.bounds
        near = np.flatnonzero(
            (placed_bounds[:, 0] - link_m <= max_x)
            & (placed_bounds[:, 2] + link_m >= min_x)
            & (placed_bounds[:, 1] - link_m <= max_y)
            & (placed_bounds[:, 3] + link_m >= min_y)
        )
        if len(near) and shapely.dwithin(piece, placed[near], link_m).any():
            return True
    return False


__all__ = ["DEFAULT_LINK_M", "RelayoutReport", "relayout_area"]
