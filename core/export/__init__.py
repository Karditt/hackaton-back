"""Экспорт (E8): GeoJSON, KML, расписание в CSV.

WGS84 (заказчик, №10, №21). Высоты — над поверхностью, KML — `relativeToGround`.
Выполнимый план: маршруты по фазам с метками времени, подобласти, зоны
манёвра, площадки; расписание — отдельным CSV (№31). Невыполнимый — отчёт:
причины с местами нарушений и рекомендации, маршрутов нет (инвариант 10).
Структура файлов описана в docs/export_format.md — это требование заказчика
(№21: «задокументируйте, как всё будет храниться»).

Не реализовано: форматы наземных станций (`.plan`, `.waypoints`) — P2.

ЗДЕСЬ ВТОРАЯ ГРАНИЦА ИНВАРИАНТА 1: метры переводятся обратно в WGS84 при
выгрузке.
"""

from __future__ import annotations

from core.export.geojson import mission_to_geojson, write_geojson
from core.export.kml import mission_to_kml, write_kml
from core.export.schedule import schedule_to_csv, write_schedule
from core.export.writer import export_mission, output_path_for

__all__ = [
    "export_mission",
    "mission_to_geojson",
    "mission_to_kml",
    "output_path_for",
    "schedule_to_csv",
    "write_geojson",
    "write_kml",
    "write_schedule",
]
