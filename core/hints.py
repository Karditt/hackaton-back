"""Подсказки по пулу техники: срок при N бортах и сколько бортов к сроку (E6).

Заказчик (№51): пользователь задаёт ограничение — число бортов или срок — и
получает подсказку: выполнимо ли, сколько времени займёт работа при заданном
числе бортов, сколько бортов нужно, чтобы успеть в срок. Флот сценария здесь —
пул доступной техники (№48): из него берутся N бортов, и для них прогоняется
обычный конвейер со всеми стадиями и краш-тестом. Подсказка — не прикидка, а
настоящие планы: ответ «хватит 4 бортов» подтверждён планом на 4 бортах.

Какие N бортов. Самые производительные: шаг галсов × путевая скорость на
галсе, м²/с — сколько площади борт снимает за секунду съёмки. Одной
производительности мало: у запретных зон бывает работа, которую самолёт не
снимет (зоне манёвра нет места), а коптер снимет. Тогда N самых
производительных — одни самолёты — дают невыполнимый план, хотя те же N
бортов с коптером вместо последнего самолёта справились бы (`real_msk`: три
Геоскан 201 не снимают 5 подобластей, два 201 и Gemini снимают всё). Поэтому
состав чинится по шагам: план провалился по покрытию — последний борт
подбора меняется на лучший борт другой строки пула, берётся замена, сильнее
всех сократившая непокрытую площадь, и так, пока площадь сокращается (на
`real_nw` работа у зон разных площадок требует коптеров у каждой). Строки
принятых замен запоминаются и в следующих подборах берутся сразу.

Прогоны — в режиме «минимум срока» и без дедлайна сценария: вопрос о том,
сколько займёт работа, а срок сравнивается с итогом. Сколько бортов нужно к
сроку — двоичный поиск по N с проверкой соседей: выполнимость по N не
обязана быть монотонной (эвристика распределения и разведение в расписании),
поэтому N − 1 проверяется всегда, и если он тоже успевает, поиск идёт ниже.

Один сценарий, один вопрос и одно зерно — один ответ (AGENT.md §8): случайности
нет, выученные строки пула зависят только от порядка прогонов, а он задан
вопросом.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from core import assumptions
from core.catalog import VehicleCatalog, load_catalog
from core.pipeline import PlanningRun, resolve_fleet, run_pipeline, scenario_catalog
from core.schemas import (
    FleetUnit,
    HintBoard,
    HintKind,
    HintReport,
    HintRun,
    OptimizationMode,
    PlanWarning,
    Scenario,
    Severity,
    Stage,
    Vehicle,
    VehicleType,
)
from core.survey.plan import build_survey_plan

#: Причины, по которым план проваливается из-за состава, а не из-за числа
#: бортов: часть работы не может снять ни один борт подбора — самолёту у зоны
#: не развернуться, коптеру не долететь.
COVERAGE_REASONS = frozenset({"allocate.cells_unserviceable", "allocate.cells_out_of_reach"})

#: Обратный вызов после каждого прогона: для прогресса фоновой задачи.
OnRun = Callable[[HintRun], None]


@dataclass(frozen=True)
class PoolBoard:
    """Один борт пула."""

    row: int
    """Строка флота сценария. Борта одной строки взаимозаменяемы."""
    unit: FleetUnit
    vehicle: Vehicle
    productivity_m2ps: float
    """Шаг галсов × путевая скорость на галсе, м²/с."""


@dataclass
class HintResult:
    """Ответ-подсказка и план, который его подтверждает."""

    report: HintReport
    plan: PlanningRun | None = None
    """Прогон на числе бортов из ответа; None — ответа нет."""


def pool_boards(
    scenario: Scenario, catalog: VehicleCatalog | None = None
) -> tuple[list[PoolBoard], list[PlanWarning]]:
    """Борта пула по убыванию производительности.

    Пул — флот сценария. Модель, которая не выполняет съёмку этого типа с
    заданным качеством, в пул не входит: в плане она не полетит при любом N.
    При равной производительности порядок — как в сценарии.

    Args:
        scenario: сценарий; его флот — пул.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.

    Returns:
        Борта пула и предупреждения о моделях, не вошедших в пул.

    Raises:
        CatalogError: во флоте модель, которой нет в каталоге.
    """
    vehicles = resolve_fleet(scenario, scenario_catalog(scenario, catalog))
    survey_plan = build_survey_plan(scenario, vehicles)
    boards: list[PoolBoard] = []
    warnings: list[PlanWarning] = []
    for row, unit in enumerate(scenario.fleet):
        vehicle = vehicles[unit.vehicle_id]
        params = survey_plan.for_vehicle(unit.vehicle_id)
        if params is None:
            warnings.append(
                PlanWarning(
                    code="hints.board_excluded",
                    severity=Severity.WARNING,
                    stage=Stage.ALLOCATE,
                    message=(
                        f"{vehicle.name} × {unit.count} не входит в пул: модель не выполняет "
                        "съёмку этого типа с заданным качеством"
                    ),
                )
            )
            continue
        productivity = params.strip_spacing_m * params.survey_speed_mps
        boards.extend(PoolBoard(row, unit, vehicle, productivity) for _ in range(unit.count))
    if not boards:
        warnings.extend(
            warning for warning in survey_plan.warnings if warning.severity is Severity.ERROR
        )
    boards.sort(key=lambda board: (-board.productivity_m2ps, board.row))
    return boards, warnings


def fleet_of(boards: Sequence[PoolBoard]) -> list[FleetUnit]:
    """Флот сценария из бортов пула: по строке на каждую строку пула, в её порядке.

    Приписка к площадке, нагрузка и предел вылетов у строки остаются как в
    сценарии, меняется только число бортов.
    """
    counts = Counter(board.row for board in boards)
    units = {board.row: board.unit for board in boards}
    return [units[row].model_copy(update={"count": counts[row]}) for row in sorted(counts)]


def boards_text(count: int, case: str = "nom") -> str:
    """Число бортов словами в падеже.

    Args:
        count: число бортов.
        case: `nom` — «1 борт, 3 борта, 5 бортов»; `gen` — «с 1 борта,
            с 3 бортов»; `loc` — «при 1 борте, при 3 бортах».
    """
    tail = count % 100
    one = count % 10 == 1 and tail != 11
    few = count % 10 in (2, 3, 4) and not 12 <= tail <= 14
    if case == "gen":
        word = "борта" if one else "бортов"
    elif case == "loc":
        word = "борте" if one else "бортах"
    else:
        word = "борт" if one else "борта" if few else "бортов"
    return f"{count} {word}"


def duration_text(seconds: float) -> str:
    """«12 ч 5 мин», «45 мин», «3 ч».

    Args:
        seconds: длительность, секунды.
    """
    hours, minutes = divmod(round(seconds / 60), 60)
    if hours == 0:
        return f"{minutes} мин"
    if minutes == 0:
        return f"{hours} ч"
    return f"{hours} ч {minutes} мин"


@dataclass
class _Session:
    """Прогоны одного вопроса: пул, выученные строки, прогоны по N."""

    scenario: Scenario
    catalog: VehicleCatalog
    deadline_s: float | None
    seed: int | None
    on_run: OnRun | None
    pool: list[PoolBoard] = field(default_factory=list)
    warnings: list[PlanWarning] = field(default_factory=list)
    required: list[int] = field(default_factory=list)
    """Строки пула, без которых область не покрыть, — выучены по ходу вопроса."""
    runs: dict[int, HintRun] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.pool, self.warnings = pool_boards(self.scenario, self.catalog)

    @property
    def size(self) -> int:
        """Бортов в пуле."""
        return len(self.pool)

    def _victim(self, chosen: list[int]) -> int | None:
        """Позиция борта, которого не жалко заменить: последний сверх выученных нужных."""
        need = Counter(self.required)
        rows = Counter(self.pool[index].row for index in chosen)
        for position in reversed(range(len(chosen))):
            row = self.pool[chosen[position]].row
            if rows[row] > need[row]:
                return position
        return None

    def _spare(self, chosen: list[int], row: int) -> int | None:
        """Лучший борт строки `row`, ещё не взятый в подбор."""
        return next(
            (
                index
                for index, board in enumerate(self.pool)
                if board.row == row and index not in chosen
            ),
            None,
        )

    def _swap_note(self, removed: PoolBoard, added: PoolBoard, why: str) -> str:
        return f"{added.vehicle.name} вместо {removed.vehicle.name}: {why}"

    def _select(self, count: int) -> tuple[list[int], str]:
        """Подбор N бортов: самые производительные плюс выученные нужные строки."""
        chosen = list(range(count))
        notes = []
        need: Counter[int] = Counter()
        for row in self.required:
            need[row] += 1
            if sum(self.pool[index].row == row for index in chosen) >= need[row]:
                continue
            spare = self._spare(chosen, row)
            victim = self._victim(chosen)
            if spare is None or victim is None:
                continue
            notes.append(
                self._swap_note(
                    self.pool[chosen[victim]],
                    self.pool[spare],
                    "без него область не покрыть",
                )
            )
            chosen[victim] = spare
        return sorted(chosen), "; ".join(notes)

    def _run(self, chosen: list[int], note: str) -> tuple[HintRun, PlanningRun]:
        """Прогнать конвейер на подборе бортов пула."""
        boards = [self.pool[index] for index in chosen]
        count = len(boards)
        scenario = self.scenario.model_copy(
            update={
                "id": f"{self.scenario.id}--fleet{count}",
                "name": f"{self.scenario.name or self.scenario.id} — {boards_text(count)}",
                "fleet": fleet_of(boards),
                "mode": OptimizationMode.MIN_MAKESPAN,
                "deadline_s": None,
            }
        )
        started = time.perf_counter()
        # Подсказка считает десятки планов: эвристика — без целевых сроков
        # (`ALLOCATE_TARGET_FACTORS`), иначе каждый прогон вдвое дольше.
        plan = run_pipeline(scenario, catalog=self.catalog, seed=self.seed, targets=False)
        elapsed_s = time.perf_counter() - started

        mission = plan.mission
        metrics = mission.metrics
        reasons: dict[str, PlanWarning] = {}
        for reason in mission.reasons:
            reasons.setdefault(reason.code, reason)
        makespan_s = metrics.makespan_s if metrics is not None else None
        run = HintRun(
            boards=count,
            fleet=scenario.fleet,
            status=mission.status,
            makespan_s=makespan_s,
            resource_s=metrics.resource_s if metrics is not None else None,
            sortie_count=metrics.sortie_count if metrics is not None else None,
            meets_deadline=(
                None
                if makespan_s is None or self.deadline_s is None
                else makespan_s <= self.deadline_s
            ),
            reasons=list(reasons.values()),
            note=note,
            elapsed_s=elapsed_s,
        )
        return run, plan

    def _candidates(self, chosen: list[int], removed: PoolBoard, run: HintRun) -> list[int]:
        """Кем заменить борт: лучший свободный борт каждой другой строки пула.

        Если часть работы не снимает ни один борт подбора из-за зоны манёвра,
        первыми пробуются мультироторы: им зона манёвра не нужна.
        """
        spares: dict[int, int] = {}
        for index, board in enumerate(self.pool):
            if index not in chosen and board.row != removed.row:
                spares.setdefault(board.row, index)
        hover_first = any(reason.code == "allocate.cells_unserviceable" for reason in run.reasons)
        return sorted(
            spares.values(),
            key=lambda index: (
                not (hover_first and self.pool[index].vehicle.type is VehicleType.MULTIROTOR),
                index,
            ),
        )

    def evaluate(self, count: int) -> tuple[HintRun, PlanningRun]:
        """Прогон на N бортах пула; при провале по покрытию — с починкой состава.

        Починка — по шагам: последний борт сверх нужных меняется на лучший
        борт другой строки пула, берётся замена, сильнее всех сократившая
        непокрытую площадь (первая, что покрыла всё, — сразу), и так, пока
        площадь сокращается. Строки принятых замен запоминаются: в следующих
        подборах они берутся сразу, без лишних прогонов.

        Args:
            count: число бортов, 1..размер пула.

        Returns:
            Прогон, попавший в отчёт, и сам план.
        """
        chosen, note = self._select(count)
        run, plan = self._run(chosen, note)
        deficit_m2 = _deficit_m2(plan)
        while deficit_m2 > 0:
            victim = self._victim(chosen)
            if victim is None:
                break
            removed = self.pool[chosen[victim]]
            reason = next(item for item in run.reasons if item.code in COVERAGE_REASONS)
            best: tuple[float, int, list[int], HintRun, PlanningRun, str] | None = None
            for index in self._candidates(chosen, removed, run):
                board = self.pool[index]
                alternative = sorted([*chosen[:victim], *chosen[victim + 1 :], index])
                why = "без замены область не покрыть"
                swap = self._swap_note(removed, board, why if note else f"{why} — {reason.message}")
                alt_run, alt_plan = self._run(alternative, "; ".join(filter(None, [note, swap])))
                alt_deficit_m2 = _deficit_m2(alt_plan)
                if alt_deficit_m2 < (best[0] if best else deficit_m2):
                    best = (alt_deficit_m2, board.row, alternative, alt_run, alt_plan, swap)
                if alt_deficit_m2 == 0:
                    break
            if best is None:
                break
            deficit_m2, row, chosen, run, plan, swap = best
            note = "; ".join(filter(None, [note, swap]))
            self.required.append(row)
        self.runs[count] = run
        if self.on_run is not None:
            self.on_run(run)
        return run, plan

    def report(
        self,
        kind: HintKind,
        answer: str,
        answer_boards: int | None = None,
        boards: int | None = None,
        warnings: Sequence[PlanWarning] = (),
    ) -> HintReport:
        """Собрать отчёт подсказки из прогонов."""
        slower = []
        best: tuple[int, float] | None = None
        for count in sorted(self.runs):
            makespan_s = self.runs[count].makespan_s
            if makespan_s is None:
                continue
            if best is not None and makespan_s > best[1]:
                # Честно: планировщик не оставляет лишний борт на земле, а
                # разведение в расписании задерживает старты (долг №54).
                slower.append(
                    PlanWarning(
                        code="hints.more_is_slower",
                        severity=Severity.WARNING,
                        stage=Stage.ALLOCATE,
                        message=(
                            f"Больше бортов — не быстрее: при {boards_text(count, 'loc')} срок "
                            f"{duration_text(makespan_s)}, при {boards_text(best[0], 'loc')} — "
                            f"{duration_text(best[1])}. Лишний борт планировщик пока не "
                            "оставляет на земле"
                        ),
                    )
                )
            elif best is None or makespan_s < best[1]:
                best = (count, makespan_s)
        return HintReport(
            scenario_id=self.scenario.id,
            kind=kind,
            boards=boards,
            deadline_s=self.deadline_s,
            pool=[
                HintBoard(
                    vehicle_id=board.vehicle.id,
                    vehicle_name=board.vehicle.name,
                    productivity_m2ps=board.productivity_m2ps,
                    site_id=board.unit.site_id,
                )
                for board in self.pool
            ],
            runs=[self.runs[count] for count in sorted(self.runs)],
            answer_boards=answer_boards,
            answer=answer,
            warnings=[*self.warnings, *warnings, *slower],
        )

    def empty_pool(self, kind: HintKind, boards: int | None = None) -> HintResult:
        """Ответ, когда в пуле нет ни одного пригодного борта."""
        why = "; ".join(warning.message for warning in self.warnings) or "флот пуст"
        return HintResult(
            self.report(kind, f"В пуле нет бортов для этой съёмки: {why}", None, boards)
        )

    def composition(self, count: int) -> str:
        """Состав прогона на N бортах в порядке пула: «3 × Геоскан 201, 1 × Геоскан Gemini»."""
        counts: Counter[str] = Counter()
        for unit in self.runs[count].fleet:
            counts[unit.vehicle_id] += unit.count
        names = {board.vehicle.id: board.vehicle.name for board in self.pool}
        return ", ".join(
            f"{counts[vehicle_id]} × {names[vehicle_id]}"
            for vehicle_id in names
            if counts[vehicle_id]
        )


def _deficit_m2(plan: PlanningRun) -> float:
    """Площадь подобластей, которые не снимет ни один борт подбора, м².

    Подобласти — из причин покрытия (`COVERAGE_REASONS`, поле `cell_ids`).
    Нарезка у разных подборов своя, поэтому сравнивается площадь, а не число.
    """
    ids: set[str] = set()
    for reason in plan.mission.reasons:
        if reason.code in COVERAGE_REASONS:
            ids.update(filter(None, reason.context.get("cell_ids", "").split(",")))
    if not ids:
        return 0.0
    decomposition = plan.result.decomposition
    if decomposition is None:
        return math.inf
    return sum(cell.area_m2 for cell in decomposition.cells if cell.id in ids)


def _verdict(session: _Session, run: HintRun) -> str:
    """Итог прогона фразой: «3 борта (3 × Геоскан 201) снимают область за 12 ч 5 мин».

    Невыполнимый — «3 борта (…): задание невыполнимо — <первая причина>». С
    дедлайном — ещё «к сроку 24 ч успевают» или «не успевают».
    """
    subject = f"{boards_text(run.boards)} ({session.composition(run.boards)})"
    if run.makespan_s is None:
        reason = run.reasons[0].message if run.reasons else "план не прошёл проверку"
        return f"{subject}: задание невыполнимо — {reason}"
    one = run.boards % 10 == 1 and run.boards % 100 != 11
    text = f"{subject} {'снимает' if one else 'снимают'} область за {duration_text(run.makespan_s)}"
    if session.deadline_s is not None:
        verb = "успевает" if one else "успевают"
        verdict = verb if run.meets_deadline else f"не {verb}"
        text += f" — к сроку {duration_text(session.deadline_s)} {verdict}"
    return text


def _session(
    scenario: Scenario,
    catalog: VehicleCatalog | None,
    deadline_s: float | None,
    seed: int | None,
    on_run: OnRun | None,
) -> _Session:
    return _Session(
        scenario=scenario,
        catalog=catalog if catalog is not None else load_catalog(),
        deadline_s=deadline_s,
        seed=seed,
        on_run=on_run,
    )


def boards_hint(
    scenario: Scenario,
    boards: int,
    catalog: VehicleCatalog | None = None,
    deadline_s: float | None = None,
    seed: int | None = None,
    on_run: OnRun | None = None,
) -> HintResult:
    """Сколько времени займёт работа при N бортах пула.

    Args:
        scenario: сценарий; его флот — пул.
        boards: число бортов N, не меньше 1. Больше пула — считается весь пул.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        deadline_s: срок от начала работ, секунды, — сказать, успевают ли.
            None — дедлайн сценария, если он есть.
        seed: зерно ГПСЧ. None — из сценария.
        on_run: вызывается после каждого прогона конвейера.

    Returns:
        Отчёт и план на N бортах.

    Raises:
        ValueError: N меньше 1.
        CatalogError, GeometryError, ScenarioError: ошибка входа.
    """
    if boards < 1:
        raise ValueError(f"Число бортов — от 1, спрошено {boards}")
    session = _session(
        scenario,
        catalog,
        deadline_s if deadline_s is not None else scenario.deadline_s,
        seed,
        on_run,
    )
    if session.size == 0:
        return session.empty_pool(HintKind.BOARDS, boards)
    warnings = []
    count = boards
    if boards > session.size:
        count = session.size
        warnings.append(
            PlanWarning(
                code="hints.pool_short",
                severity=Severity.WARNING,
                stage=Stage.ALLOCATE,
                message=(
                    f"В пуле {boards_text(session.size)}, спрошено {boards}: считается весь пул"
                ),
            )
        )
    run, plan = session.evaluate(count)
    answer = _verdict(session, run)
    return HintResult(
        session.report(
            HintKind.BOARDS,
            answer,
            count if run.makespan_s is not None else None,
            boards,
            warnings,
        ),
        plan if run.makespan_s is not None else None,
    )


def deadline_hint(
    scenario: Scenario,
    deadline_s: float,
    catalog: VehicleCatalog | None = None,
    seed: int | None = None,
    on_run: OnRun | None = None,
) -> HintResult:
    """Сколько бортов пула нужно, чтобы успеть к сроку.

    Сначала — весь пул: не успевает он — не успеет никто. Дальше двоичный
    поиск наименьшего N, при котором план выполним и срок не позже заданного,
    и проверка соседей: N − 1 (если успевает и он — поиск идёт ниже) и N + 1
    (сколько даёт ещё один борт).

    Args:
        scenario: сценарий; его флот — пул.
        deadline_s: срок от начала работ, секунды.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        seed: зерно ГПСЧ. None — из сценария.
        on_run: вызывается после каждого прогона конвейера.

    Returns:
        Отчёт и план на найденном числе бортов.

    Raises:
        ValueError: срок не больше нуля.
        CatalogError, GeometryError, ScenarioError: ошибка входа.
    """
    if deadline_s <= 0:
        raise ValueError(f"Срок — больше нуля, задано {deadline_s} с")
    session = _session(scenario, catalog, deadline_s, seed, on_run)
    if session.size == 0:
        return session.empty_pool(HintKind.DEADLINE)

    best: dict[int, PlanningRun] = {}

    def fits(count: int) -> bool:
        if count in session.runs:
            return bool(session.runs[count].meets_deadline)
        run, plan = session.evaluate(count)
        if run.meets_deadline and (not best or count < min(best)):
            best.clear()
            best[count] = plan
        return bool(run.meets_deadline)

    size = session.size
    if not fits(size):
        full = session.runs[size]
        lead = "Не успеть" if full.makespan_s is not None else "Задание не выполнить"
        answer = f"{lead} даже всем пулом: {_verdict(session, full)}"
        return HintResult(session.report(HintKind.DEADLINE, answer))

    low, high = 1, size
    while low < high:
        middle = (low + high) // 2
        if fits(middle):
            high = middle
        else:
            low = middle + 1
    count = high
    while count > 1 and fits(count - 1):
        count -= 1
    if count < size:
        fits(count + 1)

    run = session.runs[count]
    assert run.makespan_s is not None
    answer = (
        f"Чтобы успеть за {duration_text(deadline_s)}, нужно {boards_text(count)} "
        f"({session.composition(count)}): срок {duration_text(run.makespan_s)}"
    )
    if count > 1:
        answer += f". Меньше не хватит: {_verdict(session, session.runs[count - 1])}"
    return HintResult(session.report(HintKind.DEADLINE, answer, count), best.get(count))


def sweep_hint(
    scenario: Scenario,
    catalog: VehicleCatalog | None = None,
    deadline_s: float | None = None,
    seed: int | None = None,
    on_run: OnRun | None = None,
) -> HintResult:
    """Срок при каждом N — от одного борта до всего пула.

    Args:
        scenario: сценарий; его флот — пул.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        deadline_s: срок от начала работ, секунды, — назвать наименьшее N,
            которое успевает. None — дедлайн сценария, если он есть.
        seed: зерно ГПСЧ. None — из сценария.
        on_run: вызывается после каждого прогона конвейера.

    Returns:
        Отчёт и план: с дедлайном — на наименьшем успевающем N, без него — на
        N с самым коротким сроком.

    Raises:
        CatalogError, GeometryError, ScenarioError: ошибка входа.
    """
    session = _session(
        scenario,
        catalog,
        deadline_s if deadline_s is not None else scenario.deadline_s,
        seed,
        on_run,
    )
    if session.size == 0:
        return session.empty_pool(HintKind.SWEEP)

    # План держится один — лучший на сейчас: прогон на данных заказчика тяжёлый
    # (сотни подобластей, их галсы, маршруты вылетов), пятнадцать сразу — лишнее.
    kept: tuple[int, PlanningRun] | None = None
    fastest_s = float("inf")
    for count in range(1, session.size + 1):
        run, plan = session.evaluate(count)
        if run.makespan_s is None:
            continue
        if session.deadline_s is None:
            if run.makespan_s < fastest_s:
                fastest_s = run.makespan_s
                kept = (count, plan)
        elif run.meets_deadline and kept is None:
            kept = (count, plan)

    timed = {
        count: run.makespan_s for count, run in session.runs.items() if run.makespan_s is not None
    }
    if not timed:
        answer = (
            "Ни при каком числе бортов пула задание не выполнить. "
            f"Весь пул — {_verdict(session, session.runs[session.size])}"
        )
        return HintResult(session.report(HintKind.SWEEP, answer))

    first = min(timed)
    fastest = min(timed, key=lambda count: (timed[count], count))
    answer = (
        f"Выполнимо начиная с {boards_text(first, 'gen')} — {duration_text(timed[first])}; "
        f"самый короткий срок — {duration_text(timed[fastest])} "
        f"при {boards_text(fastest, 'loc')}"
    )
    # Сколько бортов реально нужно: срок почти лучший, а ресурс бывает вдвое меньше.
    limit_s = timed[fastest] * (1.0 + assumptions.HINT_NEAR_BEST_FRACTION)
    near = min(count for count in timed if timed[count] <= limit_s)
    if near < fastest:
        near_run, fast_run = session.runs[near], session.runs[fastest]
        answer += (
            f"; почти так же — {duration_text(timed[near])} — уже при {boards_text(near, 'loc')}"
        )
        if near_run.resource_s is not None and fast_run.resource_s is not None:
            answer += (
                f", ресурс {duration_text(near_run.resource_s)} "
                f"против {duration_text(fast_run.resource_s)}"
            )
    if session.deadline_s is not None:
        limit = duration_text(session.deadline_s)
        answer += (
            f". К сроку {limit} хватит {boards_text(kept[0])}"
            if kept is not None
            else f". К сроку {limit} не успеть ни при каком числе бортов пула"
        )
    target = kept[0] if kept is not None else None
    return HintResult(
        session.report(HintKind.SWEEP, answer, target), kept[1] if kept is not None else None
    )


def run_hint(
    scenario: Scenario,
    kind: HintKind,
    boards: int | None = None,
    deadline_s: float | None = None,
    catalog: VehicleCatalog | None = None,
    seed: int | None = None,
    on_run: OnRun | None = None,
) -> HintResult:
    """Ответить на вопрос подсказки — общая точка для CLI и API.

    Args:
        scenario: сценарий; его флот — пул.
        kind: вопрос.
        boards: число бортов — для `boards`.
        deadline_s: срок от начала работ, секунды, — для `deadline`, для
            остальных — необязательно.
        catalog: каталог техники. None — прочитать `data/vehicles.yaml`.
        seed: зерно ГПСЧ. None — из сценария.
        on_run: вызывается после каждого прогона конвейера.

    Raises:
        ValueError: вопросу не хватает числа бортов или срока.
    """
    if kind is HintKind.BOARDS:
        if boards is None:
            raise ValueError("Вопрос «срок при N бортах» — нужно число бортов")
        return boards_hint(scenario, boards, catalog, deadline_s, seed, on_run)
    if kind is HintKind.DEADLINE:
        if deadline_s is None:
            raise ValueError("Вопрос «сколько бортов к сроку» — нужен срок")
        return deadline_hint(scenario, deadline_s, catalog, seed, on_run)
    return sweep_hint(scenario, catalog, deadline_s, seed, on_run)


__all__ = [
    "COVERAGE_REASONS",
    "HintResult",
    "PoolBoard",
    "boards_hint",
    "boards_text",
    "deadline_hint",
    "duration_text",
    "fleet_of",
    "pool_boards",
    "run_hint",
    "sweep_hint",
]
