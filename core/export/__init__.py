"""Экспорт полётных заданий (E8): KML и GeoJSON.

Состояние: GeoJSON реализован полностью (RFC 7946), KML — базовый: фазы
полёта разложены по папкам, режим высоты выставлен явно. Не реализовано:
форматы наземных станций (`.plan` QGroundControl, `.waypoints` Mission
Planner) — они в ТЗ опциональны и режутся первыми при нехватке времени.

ЗДЕСЬ ВТОРАЯ И ПОСЛЕДНЯЯ ГРАНИЦА ИНВАРИАНТА 1: метры переводятся обратно в
WGS84 ровно в этом пакете и больше нигде.
"""

from __future__ import annotations

from core.export.geojson import mission_to_geojson, write_geojson
from core.export.kml import mission_to_kml, write_kml
from core.export.writer import export_mission, output_path_for

__all__ = [
    "export_mission",
    "mission_to_geojson",
    "mission_to_kml",
    "output_path_for",
    "write_geojson",
    "write_kml",
]
