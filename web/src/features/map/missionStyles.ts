/** Оформление слоёв плана. */

import type L from 'leaflet';
import type { FlightPhase } from '../../api';

/** Цвета фаз полёта. Те же смыслы, что в стилях KML (core/export/kml.py). */
export const PHASE_COLORS: Record<FlightPhase, string> = {
  takeoff: '#16a34a',
  climb: '#0ea5e9',
  transit: '#94a3b8',
  strip: '#dc2626',
  turn: '#f59e0b',
  descent: '#0ea5e9',
  landing: '#1d4ed8',
};

const UNKNOWN_PHASE_COLOR = '#334155';

/** Стили геометрии сценария — по полю `kind` признака GeoJSON. */
export const AREA_STYLES: Record<string, L.PathOptions> = {
  survey_area: { color: '#2563eb', weight: 2, fillOpacity: 0.06 },
  no_fly_zone: { color: '#dc2626', weight: 2, fillOpacity: 0.18, dashArray: '6 4' },
  airspace_boundary: { color: '#7c3aed', weight: 2, fillOpacity: 0, dashArray: '10 6' },
};

export const SITE_MARKER: L.CircleMarkerOptions = {
  radius: 6,
  color: '#111827',
  fillColor: '#facc15',
  fillOpacity: 1,
};

export function legStyle(phase: string | undefined): L.PathOptions {
  return {
    color: PHASE_COLORS[phase as FlightPhase] ?? UNKNOWN_PHASE_COLOR,
    weight: phase === 'strip' ? 3 : 2,
    opacity: phase === 'transit' ? 0.6 : 0.95,
  };
}
