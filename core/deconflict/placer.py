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

from shapely.prepared import PreparedGeometry, prep

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
        self._closing_zones: dict[str, list[tuple[float, PreparedGeometry]]] = {}
        self._closing: dict[tuple[str, str], list[float]] = {}

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

    def daylight_room(self, ready_s: float) -> list[float] | None:
        """Остаток окна по Солнцу и длины следующих окон, секунды; None — ограничения нет."""
        return self._book.daylight_room(ready_s)

    def closing_cells(
        self, instance: FleetInstance, cell_ids: list[str], after_s: float
    ) -> dict[str, float]:
        """Подобласти, которые временная зона закроет позже `after_s`, и когда.

        Подобласть закрывается, если её полигон или её зона манёвра у этого
        борта задевает временную зону, запрещающую высоту съёмки. До открытия
        зоны её можно снять, потом — только после закрытия.

        Args:
            instance: борт.
            cell_ids: подобласти-кандидаты.
            after_s: с какого момента смотреть, секунды от начала работ.

        Returns:
            Момент ближайшего открытия зоны над подобластью, секунды от начала
            работ; только подобласти, которые закроются позже `after_s`.
        """
        vehicle_id = instance.vehicle.id
        if vehicle_id not in self._closing_zones:
            params = self._survey_plan.for_vehicle(vehicle_id)
            assert params is not None
            self._closing_zones[vehicle_id] = [
                (opens_s, prep(zone))
                for opens_s, zone in self._book.closing_zones(params.altitude_agl_m)
            ]
        zones = self._closing_zones[vehicle_id]
        if not zones:
            return {}
        result: dict[str, float] = {}
        for cell_id in cell_ids:
            key = (cell_id, vehicle_id)
            if key not in self._closing:
                shapes = [self._oracle.cell_geometry(cell_id)]
                coverage = self._oracle.get(cell_id, vehicle_id)
                shapes.extend(polygon_to_shapely(zone) for zone in coverage.maneuver_zones)
                self._closing[key] = [
                    opens_s
                    for opens_s, zone in zones
                    if any(zone.intersects(shape) for shape in shapes)
                ]
            later = [opens_s for opens_s in self._closing[key] if opens_s > after_s]
            if later:
                result[cell_id] = min(later)
        return result

    def commit(
        self, instance: FleetInstance, site_id: str, cell_ids: list[str], start_s: float
    ) -> None:
        """Поставить вылет со взлётом в `start_s` секунд от начала работ."""
        self._book.add(self._prepare(instance, site_id, cell_ids), start_s)

    def rewind(self, count: int) -> None:
        """Оставить в книге только первые `count` поставленных вылетов."""
        self._book.rewind(count)


__all__ = ["BookPlacer", "SortieProbe"]
