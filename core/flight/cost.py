"""Сборка вылета: фазы полёта, время, линейный расход, высоты.

Модель — по ответам заказчика:

* время вылета — сумма фаз (инвариант 7); на галсе и на перелёте автопилот
  держит путевую скорость, поэтому время участка — длина, делённая на эту
  скорость (№45). Ветер во время не входит: он учтён при выборе скорости в
  модели съёмки и проверяется валидатором как выполнимость;
* расход заряда линеен по полётному времени, отсечка 20% (№34, №35);
* полезная работа — на одной высоте; высота меняется только на перелётах,
  которые идут на эшелоне внутри коридора (№10);
* площадки взлёта и посадки могут различаться (№29);
* разворот самолёта — время по радиусу разворота; геометрия петли не
  строится, вместо неё у подобласти есть защитная зона манёвра (№38).

Одна модель на всё: распределение проверяет вместимость вылета той же
функцией, что собирает итоговый вылет. Быстрой оценки-двойника больше нет.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from core import assumptions
from core.schemas import (
    CellCoverage,
    FlightPhase,
    Leg,
    MetricLineString,
    MetricPoint,
    Sortie,
    Strip,
    SurveyParams,
    Vehicle,
    Waypoint,
)

#: Как прокладывается перелёт между двумя точками на заданной высоте, м AGL.
#: Реализация — `core.transit`, чтобы модель полёта не знала про графы видимости.
TransitRouter = Callable[[MetricPoint, MetricPoint, float], MetricLineString]

#: Можно ли пройти этот перелёт между подобластями вылета на высоте съёмки, не
#: поднимаясь на эшелон. Ломаная — в метрах; решает тот, кто знает чужие
#: подобласти (конвейер), — модель полёта про них не знает.
SurveyHopCheck = Callable[[MetricLineString], bool]

#: Свободен ли участок ломаной (точки в метрах) во всём диапазоне высот
#: [нижняя, верхняя], м AGL: не задевает зон, запрещающих эти высоты, и
#: препятствий, опасных на нижней. Решает тот, кто знает зоны (конвейер).
SlopeCheck = Callable[[list[MetricPoint], float, float], bool]


def _distance_m(start: MetricPoint, end: MetricPoint) -> float:
    """Длина отрезка в метрах."""
    return math.hypot(end.x_m - start.x_m, end.y_m - start.y_m)


def _point_at(points: list[MetricPoint], cum: list[float], s: float) -> MetricPoint:
    """Точка ломаной на расстоянии `s` метров от начала (линейно внутри отрезка)."""
    if s <= 0.0:
        return points[0]
    for index in range(1, len(points)):
        if cum[index] >= s:
            span = cum[index] - cum[index - 1]
            share = (s - cum[index - 1]) / span if span > 0.0 else 0.0
            a, b = points[index - 1], points[index]
            return MetricPoint(
                x_m=a.x_m + (b.x_m - a.x_m) * share, y_m=a.y_m + (b.y_m - a.y_m) * share
            )
    return points[-1]


def _cut_polyline(
    points: list[MetricPoint], cum: list[float], s0: float, s1: float
) -> list[MetricPoint]:
    """Кусок ломаной от `s0` до `s1` метров от начала, с вершинами между ними."""
    part = [_point_at(points, cum, s0)]
    part.extend(points[index] for index in range(1, len(points) - 1) if s0 < cum[index] < s1)
    part.append(_point_at(points, cum, s1))
    return part


class _SortieBuilder:
    """Накопитель участков вылета с общим счётчиком времени и положением."""

    def __init__(self, start: MetricPoint) -> None:
        self.legs: list[Leg] = []
        self.time_s = 0.0
        self.position = start
        self.altitude_m = 0.0

    def ground(self, phase: FlightPhase, duration_s: float) -> None:
        """Наземная фаза (взлёт, посадка): длина по земле нулевая."""
        start = Waypoint(
            x_m=self.position.x_m, y_m=self.position.y_m, alt_m=0.0, time_s=self.time_s
        )
        self.time_s += duration_s
        end = Waypoint(x_m=self.position.x_m, y_m=self.position.y_m, alt_m=0.0, time_s=self.time_s)
        self.legs.append(
            Leg(
                phase=phase,
                waypoints=[start, end],
                length_m=0.0,
                duration_s=duration_s,
                ground_speed_mps=0.0,
            )
        )
        self.altitude_m = 0.0

    def vertical(self, to_alt_m: float) -> None:
        """Набор или снижение над текущей точкой. Нулевой перепад — без участка."""
        delta_m = to_alt_m - self.altitude_m
        if abs(delta_m) <= assumptions.GEOMETRY_TOLERANCE_M:
            return
        phase = FlightPhase.CLIMB if delta_m > 0 else FlightPhase.DESCENT
        rate = assumptions.CLIMB_RATE_MPS if delta_m > 0 else assumptions.DESCENT_RATE_MPS
        duration_s = abs(delta_m) / rate
        start = Waypoint(
            x_m=self.position.x_m,
            y_m=self.position.y_m,
            alt_m=self.altitude_m,
            ground_speed_mps=0.0,
            time_s=self.time_s,
        )
        self.time_s += duration_s
        end = Waypoint(
            x_m=self.position.x_m,
            y_m=self.position.y_m,
            alt_m=to_alt_m,
            ground_speed_mps=0.0,
            time_s=self.time_s,
        )
        self.legs.append(
            Leg(
                phase=phase,
                waypoints=[start, end],
                length_m=abs(delta_m),
                duration_s=duration_s,
                ground_speed_mps=0.0,
            )
        )
        self.altitude_m = to_alt_m

    def polyline(
        self,
        phase: FlightPhase,
        points: list[MetricPoint],
        speed_mps: float,
        strip_id: str | None = None,
        cell_id: str | None = None,
    ) -> None:
        """Горизонтальный участок по ломаной на текущей высоте с путевой скоростью."""
        waypoints = [
            Waypoint(
                x_m=points[0].x_m,
                y_m=points[0].y_m,
                alt_m=self.altitude_m,
                ground_speed_mps=speed_mps,
                time_s=self.time_s,
            )
        ]
        length_m = 0.0
        for start, end in zip(points, points[1:], strict=False):
            segment_m = _distance_m(start, end)
            if segment_m <= 0.0:
                continue
            length_m += segment_m
            self.time_s += segment_m / speed_mps
            waypoints.append(
                Waypoint(
                    x_m=end.x_m,
                    y_m=end.y_m,
                    alt_m=self.altitude_m,
                    ground_speed_mps=speed_mps,
                    time_s=self.time_s,
                )
            )
        if len(waypoints) < 2:
            return
        self.legs.append(
            Leg(
                phase=phase,
                waypoints=waypoints,
                length_m=length_m,
                duration_s=length_m / speed_mps,
                ground_speed_mps=speed_mps,
                strip_id=strip_id,
                cell_id=cell_id,
            )
        )
        self.position = points[-1]

    def sloped(
        self,
        points: list[MetricPoint],
        speed_mps: float,
        cruise_alt_m: float,
        end_alt_m: float,
        check: SlopeCheck,
    ) -> bool:
        """Перелёт с набором и снижением на ходу; False — нельзя, ничего не добавлено.

        Самолёт не висит: высоту он набирает и теряет по пути (долг №56).
        Набор идёт от текущей высоты до `cruise_alt_m` в начале ломаной с
        вертикальной скоростью `CLIMB_RATE_MPS`, снижение до `end_alt_m` — в
        конце, с `DESCENT_RATE_MPS`; время участка — длина на путевую скорость,
        и перепад высоты за это время ровно тот, что даёт вертикальная
        скорость. Наклонные куски режутся по `SLOPE_LEG_STEP_M` высоты, и
        каждый проверяется `check` в своём диапазоне высот: маршрутизатор
        прокладывал ломаную на эшелоне и не знает, что мешает ниже. Кусок не
        прошёл проверку — участок не строится, и вызывающий набирает высоту на
        месте, как раньше.

        Не хватает длины на весь набор и снижение (короткий переход между
        островками) — недостающая часть набирается над точкой в начале, а если
        не помещается и снижение — теряется над точкой в конце: самолёт
        кружит, а не висит, и время перелёта — max(длина / скорость, время
        вертикали), а не их сумма.

        Args:
            points: ломаная перелёта от текущего положения, метры.
            speed_mps: путевая скорость, м/с.
            cruise_alt_m: эшелон, м AGL.
            end_alt_m: высота в конце ломаной, м AGL.
            check: свободен ли кусок ломаной в диапазоне высот.
        """
        cum = [0.0]
        for a, b in zip(points, points[1:], strict=False):
            cum.append(cum[-1] + _distance_m(a, b))
        length_m = cum[-1]
        if length_m <= 0.0:
            return False
        climb_m = max(cruise_alt_m - self.altitude_m, 0.0)
        drop_m = max(cruise_alt_m - end_alt_m, 0.0)
        climb_rate, drop_rate = assumptions.CLIMB_RATE_MPS, assumptions.DESCENT_RATE_MPS
        # Сколько высоты набрать и потерять над точкой, чтобы остальное легло
        # на длину ломаной.
        excess_m = speed_mps * (climb_m / climb_rate + drop_m / drop_rate) - length_m
        spiral_up_m = min(climb_m, max(excess_m, 0.0) * climb_rate / speed_mps)
        excess_m -= spiral_up_m * speed_mps / climb_rate
        spiral_down_m = min(drop_m, max(excess_m, 0.0) * drop_rate / speed_mps)
        climb_m -= spiral_up_m
        drop_m -= spiral_down_m
        start_alt_m = self.altitude_m + spiral_up_m
        climb_run_m = min(speed_mps * climb_m / climb_rate, length_m)
        drop_run_m = min(speed_mps * drop_m / drop_rate, length_m - climb_run_m)

        # Куски: (от, до) по длине ломаной и высоты на концах.
        pieces: list[tuple[float, float, float, float]] = []
        steps = max(1, math.ceil(climb_m / assumptions.SLOPE_LEG_STEP_M)) if climb_m > 0 else 0
        for index in range(steps):
            pieces.append(
                (
                    climb_run_m * index / steps,
                    climb_run_m * (index + 1) / steps,
                    start_alt_m + climb_m * index / steps,
                    start_alt_m + climb_m * (index + 1) / steps,
                )
            )
        if length_m - drop_run_m > climb_run_m:
            pieces.append((climb_run_m, length_m - drop_run_m, cruise_alt_m, cruise_alt_m))
        steps = max(1, math.ceil(drop_m / assumptions.SLOPE_LEG_STEP_M)) if drop_m > 0 else 0
        base = length_m - drop_run_m
        for index in range(steps):
            pieces.append(
                (
                    base + drop_run_m * index / steps,
                    base + drop_run_m * (index + 1) / steps,
                    cruise_alt_m - drop_m * index / steps,
                    cruise_alt_m - drop_m * (index + 1) / steps,
                )
            )

        cut = [(_cut_polyline(points, cum, s0, s1), s0, s1, a0, a1) for s0, s1, a0, a1 in pieces]
        for part, _s0, _s1, a0, a1 in cut:
            if a0 != a1 and not check(part, min(a0, a1), max(a0, a1)):
                return False

        self.vertical(start_alt_m)
        for part, s0, s1, a0, a1 in cut:
            span = s1 - s0
            walked = 0.0
            waypoints = [
                Waypoint(
                    x_m=part[0].x_m,
                    y_m=part[0].y_m,
                    alt_m=a0,
                    ground_speed_mps=speed_mps,
                    time_s=self.time_s,
                )
            ]
            for a, b in zip(part, part[1:], strict=False):
                segment_m = _distance_m(a, b)
                if segment_m <= 0.0:
                    continue
                walked += segment_m
                self.time_s += segment_m / speed_mps
                share = min(walked / span, 1.0) if span > 0.0 else 1.0
                waypoints.append(
                    Waypoint(
                        x_m=b.x_m,
                        y_m=b.y_m,
                        alt_m=a0 + (a1 - a0) * share,
                        ground_speed_mps=speed_mps,
                        time_s=self.time_s,
                    )
                )
            if len(waypoints) < 2:
                continue
            waypoints[-1] = waypoints[-1].model_copy(update={"alt_m": a1})
            if a1 > a0:
                phase = FlightPhase.CLIMB
            elif a1 < a0:
                phase = FlightPhase.DESCENT
            else:
                phase = FlightPhase.TRANSIT
            self.legs.append(
                Leg(
                    phase=phase,
                    waypoints=waypoints,
                    length_m=walked,
                    duration_s=walked / speed_mps,
                    ground_speed_mps=speed_mps,
                )
            )
        self.position = points[-1]
        self.altitude_m = end_alt_m + spiral_down_m
        self.vertical(end_alt_m)
        return True

    def turn(self, end: MetricPoint, length_m: float, speed_mps: float, cell_id: str) -> None:
        """Разворот между галсами: время по длине манёвра, геометрия — отрезок.

        TODO(E4): траектория петли не строится намеренно — заказчик просил
        заменить её защитной зоной манёвра (№38); время считается по длине.
        """
        if length_m <= assumptions.GEOMETRY_TOLERANCE_M:
            self.position = end
            return
        duration_s = length_m / speed_mps
        start = Waypoint(
            x_m=self.position.x_m,
            y_m=self.position.y_m,
            alt_m=self.altitude_m,
            ground_speed_mps=speed_mps,
            time_s=self.time_s,
        )
        self.time_s += duration_s
        finish = Waypoint(
            x_m=end.x_m,
            y_m=end.y_m,
            alt_m=self.altitude_m,
            ground_speed_mps=speed_mps,
            time_s=self.time_s,
        )
        self.legs.append(
            Leg(
                phase=FlightPhase.TURN,
                waypoints=[start, finish],
                length_m=length_m,
                duration_s=duration_s,
                ground_speed_mps=speed_mps,
                cell_id=cell_id,
            )
        )
        self.position = end


def loop_length_m(turn_radius_m: float, lateral_m: float, along_m: float = 0.0) -> float:
    """Длина разворота самолёта на встречный курс, метры (долг №61).

    Соседний галс — на `lateral_m` вбок, его начало — на `along_m` вдоль курса
    от конца текущего. Шаг не меньше двух радиусов: четверть круга, прямая
    `lateral − 2R`, четверть круга — πR + (d − 2R). Шаг меньше двух радиусов:
    полуокружностью на соседний галс не попасть, нужна петля «омега» — отворот
    от соседнего галса, дуга большого круга, отворот обратно:
    R·(π + 4·arccos((2R + d) / 4R)); при d = 2R это та же полуокружность, при
    d = 0 — разворот на ту же линию, 7πR/3. Выпуклость петли наружу — R − d/2,
    её и держит зона манёвра (`core.coverage.strips._turn_zone`). Сдвиг вдоль
    курса проходится по прямой — оценка сверху, а не снизу: план не
    оптимистичнее самолёта.

    Args:
        turn_radius_m: радиус разворота, метры.
        lateral_m: расстояние между галсами поперёк курса, метры.
        along_m: сдвиг начала следующего галса вдоль курса, метры.

    Returns:
        Длина манёвра, метры.
    """
    radius = turn_radius_m
    lateral = abs(lateral_m)
    if lateral >= 2.0 * radius:
        loop = math.pi * radius + lateral - 2.0 * radius
    else:
        loop = radius * (math.pi + 4.0 * math.acos((2.0 * radius + lateral) / (4.0 * radius)))
    return loop + abs(along_m)


def interlace_step(turn_radius_m: float, spacing_m: float) -> int:
    """Через сколько галсов самолёту выгоднее переходить, чем на соседний.

    Шаг галсов меньше двух радиусов разворота — на соседний галс только
    петлёй «омега» (`loop_length_m`). Если облетать галсы вперемежку — сначала
    каждый k-й в одну сторону, затем пропущенные на обратном пути, — переход
    между ними шире двух радиусов и проходится полукругом с прямой вставкой:
    πR + (k·d − 2R). Петля остаётся одна на смену серии.

    Вперемежку самолёт выходит из подобласти у той же стороны, где вошёл, и
    связка с соседней подобластью может стать длиннее. Поэтому облёт через
    галс берётся, только если переход короче петли хотя бы на
    `INTERLACE_MIN_GAIN` (на данных заказчика шаг 205 м при радиусе 128 м даёт
    12% — и срок плана от облёта вперемежку рос на 6%).

    Args:
        turn_radius_m: радиус разворота, метры (0 — мультиротор).
        spacing_m: шаг галсов, метры.

    Returns:
        k ≥ 1: 1 — обычная змейка по соседним галсам.
    """
    if turn_radius_m <= 0.0 or spacing_m <= 0.0 or spacing_m >= 2.0 * turn_radius_m:
        return 1
    step = math.ceil(2.0 * turn_radius_m / spacing_m)
    wide = loop_length_m(turn_radius_m, step * spacing_m)
    if wide <= (1.0 - assumptions.INTERLACE_MIN_GAIN) * loop_length_m(turn_radius_m, spacing_m):
        return step
    return 1


def turn_length_m(
    vehicle: Vehicle,
    start: MetricPoint,
    end: MetricPoint,
    heading: tuple[float, float] | None = None,
    next_heading: tuple[float, float] | None = None,
) -> float:
    """Длина перехода между концом галса и началом следующего, метры.

    Мультиротор переходит по прямой. Самолёт на встречный галс
    разворачивается петлёй (`loop_length_m`); на следующий кусок того же галса
    за отверстием (курс тот же) — летит по прямой. Без направления галса весь
    промежуток считается боковым.

    Args:
        vehicle: борт.
        start: конец галса, метры.
        end: начало следующего галса, метры.
        heading: единичный вектор курса на законченном галсе; None — неизвестен.
        next_heading: единичный вектор курса на следующем галсе; None — встречный.
    """
    gap_m = _distance_m(start, end)
    if not vehicle.is_fixed_wing() or vehicle.turn_radius_m <= 0.0:
        return gap_m
    if heading is None:
        return loop_length_m(vehicle.turn_radius_m, gap_m)
    dx, dy = end.x_m - start.x_m, end.y_m - start.y_m
    along = dx * heading[0] + dy * heading[1]
    lateral = abs(dx * heading[1] - dy * heading[0])
    if next_heading is not None and heading[0] * next_heading[0] + heading[1] * next_heading[1] > 0:
        # Курс тот же: следующий кусок того же галса впереди — по прямой;
        # иначе (галс через пустую линию) — полный круг, оценка сверху.
        if lateral <= assumptions.GEOMETRY_TOLERANCE_M and along >= 0.0:
            return gap_m
        return 2.0 * math.pi * vehicle.turn_radius_m + gap_m
    return loop_length_m(vehicle.turn_radius_m, lateral, along)


def strip_heading(strip: Strip, backward: bool = False) -> tuple[float, float] | None:
    """Единичный вектор курса на галсе в порядке облёта; None — галс нулевой длины."""
    start, end = strip.start(), strip.end()
    if backward:
        start, end = end, start
    length = _distance_m(start, end)
    if length <= 0.0:
        return None
    return ((end.x_m - start.x_m) / length, (end.y_m - start.y_m) / length)


def _strip_points(strip: Strip, backward: bool) -> list[MetricPoint]:
    """Точки галса в порядке облёта: как построен или задом наперёд."""
    points = list(strip.line.points)
    return points[::-1] if backward else points


#: Концы змейки подобласти: вход (начало первого галса) и выход (конец
#: последнего), метры. None — галсов нет.
CellEnds = tuple[MetricPoint, MetricPoint] | None


def pass_directions(
    ends: list[CellEnds], start: MetricPoint, end: MetricPoint
) -> tuple[list[bool], float]:
    """Направление облёта каждой подобласти и длина связок между ними.

    Змейку подобласти можно пройти как построена — вход в начале первого
    галса, выход в конце последнего, — или задом наперёд: вход в конце
    последнего галса, галсы в обратном порядке и каждый в обратную сторону.
    Геометрия и зоны манёвра при этом те же (концы галсов и соседи у концов не
    меняются), меняются только вход и выход. Направления выбираются
    динамическим программированием так, чтобы сумма прямых от площадки взлёта
    через входы и выходы подобластей до площадки посадки была наименьшей.

    Args:
        ends: концы змейки каждой подобласти в порядке облёта.
        start: площадка взлёта, метры.
        end: площадка посадки, метры.

    Returns:
        Флаг «задом наперёд» на каждую подобласть по порядку и сумма прямых
        связок (без галсов), метры.
    """
    # cost[d] — лучшая длина до выхода текущей подобласти в направлении d.
    cost = (0.0, math.inf)
    exits = (start, start)
    choices: list[tuple[int, int]] = []
    for item in ends:
        if item is None:
            choices.append((0, 0))
            continue
        entry_exit = ((item[0], item[1]), (item[1], item[0]))
        new_cost = []
        back = []
        for entry, _exit in entry_exit:
            options = [cost[prev] + _distance_m(exits[prev], entry) for prev in (0, 1)]
            best = 0 if options[0] <= options[1] else 1
            new_cost.append(options[best])
            back.append(best)
        cost = (new_cost[0], new_cost[1])
        exits = (item[1], item[0])
        choices.append((back[0], back[1]))

    final = [cost[d] + _distance_m(exits[d], end) for d in (0, 1)]
    direction = 0 if final[0] <= final[1] else 1
    length_m = final[direction]
    flags = [False] * len(ends)
    for index in range(len(ends) - 1, -1, -1):
        if ends[index] is None:
            continue
        flags[index] = direction == 1
        direction = choices[index][direction]
    return flags, length_m


def cell_ends(cell: CellCoverage) -> CellEnds:
    """Вход и выход змейки подобласти в том направлении, как она построена."""
    if not cell.strips:
        return None
    return cell.strips[0].start(), cell.strips[-1].end()


def cell_passes(cells: list[CellCoverage], start: MetricPoint, end: MetricPoint) -> list[bool]:
    """Направление облёта каждой подобласти вылета: True — задом наперёд.

    Args:
        cells: покрытия подобластей в порядке облёта.
        start: площадка взлёта, метры.
        end: площадка посадки, метры.

    Returns:
        Флаг на каждую подобласть по порядку (`pass_directions`).
    """
    return pass_directions([cell_ends(cell) for cell in cells], start, end)[0]


def build_sortie(
    sortie_id: str,
    vehicle: Vehicle,
    params: SurveyParams,
    takeoff_site_id: str,
    takeoff_site: MetricPoint,
    landing_site_id: str,
    landing_site: MetricPoint,
    cells: list[CellCoverage],
    transit_altitude_m: float,
    router: TransitRouter,
    own_area: BaseGeometry | None = None,
    vehicle_instance: int = 0,
    trip_index: int = 0,
    survey_hop: SurveyHopCheck | None = None,
    slope_check: SlopeCheck | None = None,
) -> Sortie:
    """Собрать один вылет от взлёта до посадки. Время — от момента взлёта.

    Между подобластями одного вылета борт идёт на высоте съёмки, если прямая
    не выходит за его собственные подобласти, либо если перелёт в обход
    запретного на высоте съёмки разрешён `survey_hop` (он не подходит к чужим
    подобластям — там на той же высоте может работать другой борт). Иначе —
    через свой эшелон (инвариант 9 — разведение по вертикали).

    Каждую подобласть борт проходит в том направлении змейки, которое короче
    связывает её с соседями по вылету (`cell_passes`). Самолёт набирает и
    теряет высоту на ходу, если перелёт это позволяет (`_SortieBuilder.sloped`);
    мультиротор — на месте.

    Args:
        sortie_id: идентификатор вылета.
        vehicle: борт.
        params: параметры съёмки борта (высота, путевые скорости).
        takeoff_site_id: идентификатор площадки взлёта.
        takeoff_site: площадка взлёта, метры.
        landing_site_id: идентификатор площадки посадки.
        landing_site: площадка посадки, метры.
        cells: покрытия подобластей в порядке облёта.
        transit_altitude_m: эшелон перелёта, м AGL.
        router: маршрутизатор перелётов.
        own_area: объединение подобластей вылета, метры. None — между
            подобластями всегда через эшелон.
        vehicle_instance: номер экземпляра борта внутри строки флота.
        trip_index: номер вылета этого борта, с нуля.
        survey_hop: можно ли перелёт между подобластями, выходящий за свои
            подобласти, пройти на высоте съёмки. None — нельзя, через эшелон.
        slope_check: свободен ли кусок перелёта в диапазоне высот — нужен,
            чтобы самолёт набирал и терял высоту на ходу. None — высота
            набирается на месте у всех типов.

    Returns:
        Вылет со временем, отсчитанным от взлёта (`start_time_s` = 0).
        Расписание сдвигает его на общую ось времени (`retime_sortie`).
    """
    survey_alt_m = params.altitude_agl_m
    hop_area = own_area.buffer(assumptions.BOUNDARY_TOLERANCE_M) if own_area is not None else None
    builder = _SortieBuilder(takeoff_site)
    backward = cell_passes(cells, takeoff_site, landing_site)

    def via_level(target: MetricPoint, end_alt_m: float) -> None:
        """Перелёт через эшелон. Самолёт набирает и теряет высоту на ходу,
        если ломаная это позволяет (`_SortieBuilder.sloped`), иначе — на месте."""
        line = router(builder.position, target, transit_altitude_m)
        if (
            slope_check is not None
            and vehicle.is_fixed_wing()
            and builder.sloped(
                line.points, params.transit_speed_mps, transit_altitude_m, end_alt_m, slope_check
            )
        ):
            return
        builder.vertical(transit_altitude_m)
        builder.polyline(FlightPhase.TRANSIT, line.points, params.transit_speed_mps)
        builder.vertical(end_alt_m)

    builder.ground(FlightPhase.TAKEOFF, assumptions.TAKEOFF_TIME_S)

    for cell, reverse in zip(cells, backward, strict=True):
        if not cell.strips:
            continue
        strips = cell.strips[::-1] if reverse else cell.strips
        entry = _strip_points(strips[0], reverse)[0]
        on_survey_level = abs(builder.altitude_m - survey_alt_m) <= assumptions.GEOMETRY_TOLERANCE_M
        hop = LineString([(builder.position.x_m, builder.position.y_m), (entry.x_m, entry.y_m)])
        if on_survey_level and hop_area is not None and hop.within(hop_area):
            builder.polyline(
                FlightPhase.TRANSIT,
                [builder.position, entry],
                params.transit_speed_mps,
                cell_id=cell.cell_id,
            )
        else:
            low_line = (
                router(builder.position, entry, survey_alt_m)
                if on_survey_level and survey_hop is not None
                else None
            )
            if low_line is not None and survey_hop is not None and survey_hop(low_line):
                # Между островками — над землёй вне области, на высоте съёмки:
                # подъём на эшелон и спуск обратно не нужны.
                builder.polyline(FlightPhase.TRANSIT, low_line.points, params.transit_speed_mps)
            else:
                via_level(entry, survey_alt_m)

        for index, strip in enumerate(strips):
            points = _strip_points(strip, reverse)
            if index > 0:
                target = points[0]
                hop = LineString(
                    [(builder.position.x_m, builder.position.y_m), (target.x_m, target.y_m)]
                )
                if hop_area is None or hop.within(hop_area):
                    builder.turn(
                        target,
                        turn_length_m(
                            vehicle,
                            builder.position,
                            target,
                            strip_heading(strips[index - 1], reverse),
                            strip_heading(strip, reverse),
                        ),
                        params.transit_speed_mps,
                        cell.cell_id,
                    )
                else:
                    # Переход через отверстие подобласти (зону, препятствие) —
                    # в обход, на той же высоте съёмки.
                    line = router(builder.position, target, survey_alt_m)
                    builder.polyline(
                        FlightPhase.TRANSIT,
                        line.points,
                        params.transit_speed_mps,
                        cell_id=cell.cell_id,
                    )
            # Скорость — своя у галсов подобласти: поперёк ветра борт удержит
            # больше, чем на любом курсе (`axis_survey_speed_mps`, долг №52).
            builder.polyline(
                FlightPhase.STRIP,
                points,
                strip.speed_mps,
                strip_id=strip.id,
                cell_id=cell.cell_id,
            )

    via_level(landing_site, 0.0)
    builder.ground(FlightPhase.LANDING, assumptions.LANDING_TIME_S)

    duration_s = builder.time_s
    return Sortie(
        id=sortie_id,
        vehicle_id=vehicle.id,
        vehicle_instance=vehicle_instance,
        takeoff_site_id=takeoff_site_id,
        landing_site_id=landing_site_id,
        trip_index=trip_index,
        legs=builder.legs,
        start_time_s=0.0,
        duration_s=duration_s,
        # Набор и снижение на месте в дальность не входят; на ходу (у самолёта)
        # — входят горизонтальной длиной, как перелёт.
        distance_m=sum(
            leg.length_m
            for leg in builder.legs
            if leg.phase not in _VERTICAL or (leg.ground_speed_mps or 0.0) > 0.0
        ),
        energy_fraction_used=duration_s / vehicle.endurance_s,
        survey_altitude_m=survey_alt_m,
        transit_altitude_m=transit_altitude_m,
        cell_ids=[cell.cell_id for cell in cells],
        strip_ids=[leg.strip_id for leg in builder.legs if leg.strip_id is not None],
    )


_VERTICAL = (FlightPhase.CLIMB, FlightPhase.DESCENT)


def retime_sortie(sortie: Sortie, start_time_s: float) -> Sortie:
    """Перенести вылет на общую ось времени: взлёт в момент `start_time_s`.

    Args:
        sortie: вылет.
        start_time_s: момент взлёта от начала работ, секунды.

    Returns:
        Копия вылета со сдвинутыми временами всех точек.
    """
    delta_s = start_time_s - sortie.start_time_s
    legs = [
        leg.model_copy(
            update={
                "waypoints": [
                    waypoint.model_copy(update={"time_s": waypoint.time_s + delta_s})
                    for waypoint in leg.waypoints
                ]
            }
        )
        for leg in sortie.legs
    ]
    return sortie.model_copy(update={"legs": legs, "start_time_s": start_time_s})


def endurance_limit_s(vehicle: Vehicle) -> float:
    """Предельная длительность вылета с учётом отсечки заряда, секунды."""
    return vehicle.endurance_s * (1.0 - vehicle.battery_reserve_fraction)


def exceeds_range(sortie: Sortie, vehicle: Vehicle) -> bool:
    """True — маршрут вылета длиннее паспортной протяжённости для этой модели.

    Ограничение живёт отдельно от заряда: у самолёта запас хода может кончиться
    раньше, чем автономность, и наоборот. Борт без `max_range_m` в каталоге
    (Геоскан 801, Gemini — числа нет в документе) по протяжённости не
    ограничивается: выдумывать предел нельзя (инвариант 6).
    """
    return vehicle.max_range_m is not None and sortie.distance_m > vehicle.max_range_m


def sortie_is_feasible(sortie: Sortie, vehicle: Vehicle) -> tuple[bool, str]:
    """Проверить вылет по заряду: линейная модель, отсечка 20% (№34, №35).

    Протяжённость маршрута здесь НЕ проверяется — она отдельным правилом,
    `exceeds_range`, чтобы причина невыполнимости называлась своим именем.

    Args:
        sortie: собранный вылет.
        vehicle: борт из каталога.

    Returns:
        Пара «выполним, причина». Причина пуста, если вылет выполним.
    """
    available = 1.0 - vehicle.battery_reserve_fraction
    if sortie.energy_fraction_used > available:
        return False, (
            f"полётное время {sortie.duration_s / 60:.0f} мин — это "
            f"{sortie.energy_fraction_used * 100:.0f}% автономности, а доступно "
            f"{available * 100:.0f}% (отсечка {vehicle.battery_reserve_fraction * 100:.0f}%)"
        )
    return True, ""


__all__ = [
    "CellEnds",
    "SurveyHopCheck",
    "TransitRouter",
    "build_sortie",
    "cell_ends",
    "cell_passes",
    "endurance_limit_s",
    "exceeds_range",
    "interlace_step",
    "loop_length_m",
    "pass_directions",
    "retime_sortie",
    "sortie_is_feasible",
    "strip_heading",
    "turn_length_m",
]
