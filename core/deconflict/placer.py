"""Книга расписания для распределения (E14 → E6, долг №54).

Эвристика распределения собирает вылеты по очереди «кто раньше освободится»,
а расписание потом сдвигает старты ради разведения — и самолёты на одной
высоте съёмки в соседних подобластях ждали друг друга часами, чего
распределение не видело. Здесь книга расписания (`ScheduleBook`) открыта
распределению: вылет ставится в неё сразу при сборке, и распределение знает
его настоящий старт.

Вылет задаётся так, как его видит распределение: борт, площадка, подобласти
по порядку. Собирается он той же моделью полёта (`probe` — проба, которой
распределение оценило длительность), покрытие подобластей — из того же
оракула, что даст итоговое покрытие. Поэтому расписание, поставив итоговые
вылеты в том же порядке, получает те же старты.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from shapely.prepared import prep

from core import assumptions
from core.allocate.fleet import FleetInstance
from core.coverage.oracle import CoverageOracle
from core.deconflict.schedule import PreparedSortie, ScheduleBook
from core.geo.project import polygon_to_shapely
from core.schemas import CoverageSet, CrsInfo, Sortie, SurveyPlan

#: Вылет с площадки на неё же по подобластям: борт, площадка, подобласти.
SortieProbe = Callable[[FleetInstance, str, list[str]], Sortie]

#: Сколько последних вылетов держать подготовленными. Эвристика ставит в
#: расписание вылет, который только что собрала и оценила; локальный поиск
#: переставляет хвост плана и ставит заново вылеты, которые уже ставил. Размер
#: кеша на результат не влияет — только на то, сколько раз вылет собирается
#: заново.
_PREPARED_CACHE_SIZE = 1024


class BookPlacer:
    """Реализация `core.allocate.greedy.SortiePlacer` поверх `ScheduleBook`."""

    def __init__(
        self,
        book: ScheduleBook,
        probe: SortieProbe,
        oracle: CoverageOracle,
        survey_plan: SurveyPlan,
        crs: CrsInfo,
    ) -> None:
        """Книга для распределения.

        Args:
            book: книга расписания, пустая.
            probe: сборка вылета моделью полёта.
            oracle: покрытия «подобласть × борт» — галсы и зоны манёвра.
            survey_plan: параметры съёмки бортов (высота съёмки).
            crs: рабочая проекция.
        """
        self._book = book
        self._probe = probe
        self._oracle = oracle
        self._survey_plan = survey_plan
        self._crs = crs
        self._prepared: OrderedDict[tuple[str, int, str, tuple[str, ...]], PreparedSortie] = (
            OrderedDict()
        )

    def _prepare(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str]
    ) -> PreparedSortie:
        key = (*instance.key, site_id, tuple(cell_ids))
        if key in self._prepared:
            self._prepared.move_to_end(key)
            return self._prepared[key]
        coverage = CoverageSet(
            crs=self._crs,
            cells=[self._oracle.get(cell_id, instance.vehicle.id) for cell_id in cell_ids],
            work_area_m2=0.0,
            covered_area_m2=0.0,
        )
        prepared = self._book.prepare(
            self._probe(instance, site_id, cell_ids), instance.vehicle.is_fixed_wing(), coverage
        )
        self._prepared[key] = prepared
        if len(self._prepared) > _PREPARED_CACHE_SIZE:
            self._prepared.popitem(last=False)
        return prepared

    def earliest_start(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str], ready_s: float
    ) -> float | None:
        """Самый ранний взлёт не раньше `ready_s` без сближений, секунды; None — окна нет."""
        return self._book.earliest_start(self._prepare(instance, site_id, cell_ids), ready_s)

    def blocking_cells(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str], start_s: float
    ) -> set[str]:
        """Подобласти вылета, из-за которых нельзя взлететь в `start_s` секунд."""
        return self._book.blocking_cells(self._prepare(instance, site_id, cell_ids), start_s)

    def crowded_cells(
        self, instance: FleetInstance, cell_ids: list[str], from_s: float, to_s: float
    ) -> set[str]:
        """Подобласти, занятые в этот промежуток другими самолётами или временными зонами.

        Подобласть занята, если её полигон или её зона манёвра у этого борта
        ближе минимума разделения к чужой зоне манёвра, занятой хоть часть
        промежутка на высоте ближе вертикального интервала, или задевает
        временную зону, которая в промежутке запрещает высоту съёмки.

        Args:
            instance: борт.
            cell_ids: подобласти-кандидаты.
            from_s: начало промежутка, секунды от начала работ.
            to_s: конец промежутка, секунды от начала работ.
        """
        params = self._survey_plan.for_vehicle(instance.vehicle.id)
        assert params is not None
        busy = self._book.busy_footprint(params.altitude_agl_m, from_s, to_s)
        closed = self._book.closed_footprint(params.altitude_agl_m, from_s, to_s)
        if busy.is_empty and closed.is_empty:
            return set()
        # Зоны манёвра в книге — только у самолётов: разделение самолётное.
        near = prep(busy.buffer(assumptions.separation_minimum_m(is_fixed_wing=True)).union(closed))
        crowded: set[str] = set()
        for cell_id in cell_ids:
            shapes = [self._oracle.cell_geometry(cell_id)]
            coverage = self._oracle.get(cell_id, instance.vehicle.id)
            shapes.extend(polygon_to_shapely(zone) for zone in coverage.maneuver_zones)
            if any(near.intersects(shape) for shape in shapes):
                crowded.add(cell_id)
        return crowded

    def commit(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str], start_s: float
    ) -> None:
        """Поставить вылет со взлётом в `start_s` секунд от начала работ."""
        self._book.add(self._prepare(instance, site_id, cell_ids), start_s)

    def rewind(self, count: int) -> None:
        """Оставить в книге только первые `count` поставленных вылетов."""
        self._book.rewind(count)


__all__ = ["BookPlacer", "SortieProbe"]
