/** Оформление слоёв. Смысл слоя — поле `kind` признака GeoJSON (docs/export_format.md). */

import type L from 'leaflet';
import type { FlightPhase } from '../../api';

/** Цвета фаз полёта. Те же смыслы, что в стилях KML (core/export/kml.py). */
export const PHASE_COLORS: Record<FlightPhase, string> = {
  takeoff: '#16a34a',
  climb: '#0ea5e9',
  transit: '#94a3b8',
  strip: '#dc2626',
  turn: '#f59e0b',
  hold: '#c026d3',
  descent: '#0ea5e9',
  landing: '#1d4ed8',
};

const UNKNOWN_PHASE_COLOR = '#334155';

/** Стили площадных и линейных слоёв по полю `kind`. */
export const KIND_STYLES: Record<string, L.PathOptions> = {
  survey_area: { color: '#2563eb', weight: 2, fillOpacity: 0.04 },
  survey_cell: { color: '#2563eb', weight: 1, opacity: 0.4, fillOpacity: 0.03 },
  maneuver_zone: { color: '#f59e0b', weight: 1, opacity: 0.5, fillOpacity: 0.08, dashArray: '2 4' },
  no_fly_zone: { color: '#dc2626', weight: 2, fillOpacity: 0.18, dashArray: '6 4' },
  obstacle: { color: '#7f1d1d', weight: 2, fillOpacity: 0.4 },
  airspace_boundary: { color: '#7c3aed', weight: 2, fillOpacity: 0, dashArray: '10 6' },
  violation: { color: '#dc2626', weight: 6, opacity: 0.9 },
};

/** Маркеры точечных слоёв по полю `kind`. */
export const POINT_MARKERS: Record<string, L.CircleMarkerOptions> = {
  site: { radius: 6, color: '#111827', fillColor: '#facc15', fillOpacity: 1 },
  obstacle: { radius: 5, color: '#7f1d1d', fillColor: '#ef4444', fillOpacity: 1 },
  recommended_site: {
    radius: 9,
    color: '#2563eb',
    fillColor: '#bfdbfe',
    fillOpacity: 0.9,
    dashArray: '3 3',
  },
};

export const DEFAULT_MARKER: L.CircleMarkerOptions = {
  radius: 4,
  color: '#334155',
  fillColor: '#94a3b8',
  fillOpacity: 1,
};

export function legStyle(phase: string | undefined): L.PathOptions {
  return {
    color: PHASE_COLORS[phase as FlightPhase] ?? UNKNOWN_PHASE_COLOR,
    weight: phase === 'strip' ? 3 : 2,
    opacity: phase === 'transit' ? 0.6 : 0.95,
  };
}
