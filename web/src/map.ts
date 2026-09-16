/**
 * Карта: подложка и отрисовка GeoJSON, пришедшего с сервера.
 *
 * Подложка — OpenStreetMap с ОБЯЗАТЕЛЬНОЙ атрибуцией «© OpenStreetMap
 * contributors» (лицензия ODbL, PROJECT_BRIEF.md §7). Сервисы Google
 * запрещены брифом.
 *
 * ОФЛАЙН-ФОЛБЭК. На площадке демонстрации может не быть интернета, поэтому
 * адрес тайлов задаётся переменной VITE_TILE_URL и может указывать на
 * локальный каталог. TODO(E10): сами тайлы тестовых областей ещё не скачаны —
 * это долг №3 в docs/current_state.md. Пока интернета нет, карта покажет
 * серый фон, но геометрия и маршруты отрисуются поверх него.
 */

import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { GeoJsonDocument } from './api';

const OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>';

/** Цвета фаз полёта. Те же смыслы, что в стилях KML (core/export/kml.py). */
const PHASE_COLORS: Record<string, string> = {
  takeoff: '#16a34a',
  climb: '#0ea5e9',
  transit: '#94a3b8',
  strip: '#dc2626',
  turn: '#f59e0b',
  descent: '#0ea5e9',
  landing: '#1d4ed8',
};

const LAYER_STYLES: Record<string, L.PathOptions> = {
  survey_area: { color: '#2563eb', weight: 2, fillOpacity: 0.06 },
  no_fly_zone: { color: '#dc2626', weight: 2, fillOpacity: 0.18, dashArray: '6 4' },
  airspace_boundary: { color: '#7c3aed', weight: 2, fillOpacity: 0, dashArray: '10 6' },
};

export function createMap(container: HTMLElement): L.Map {
  const map = L.map(container, { preferCanvas: true }).setView([59.9, 30.3], 12);
  L.tileLayer(import.meta.env.VITE_TILE_URL ?? OSM_URL, {
    attribution: OSM_ATTRIBUTION,
    maxZoom: 18,
  }).addTo(map);
  return map;
}

/**
 * Отрисовать документ GeoJSON, полученный от API.
 *
 * @param map карта Leaflet
 * @param document выгрузка плана в WGS84
 * @returns слой, который вызывающий код снимет перед следующей отрисовкой
 */
export function drawMission(map: L.Map, document: GeoJsonDocument): L.GeoJSON {
  const layer = L.geoJSON(document as never, {
    style: (feature) => {
      const properties = (feature?.properties ?? {}) as Record<string, string>;
      if (properties.kind && LAYER_STYLES[properties.kind]) {
        return LAYER_STYLES[properties.kind];
      }
      const phase = properties.phase;
      return {
        color: PHASE_COLORS[phase] ?? '#334155',
        weight: phase === 'strip' ? 3 : 2,
        opacity: phase === 'transit' ? 0.6 : 0.95,
      };
    },
    pointToLayer: (_feature, latlng) =>
      L.circleMarker(latlng, { radius: 6, color: '#111827', fillColor: '#facc15', fillOpacity: 1 }),
    onEachFeature: (feature, featureLayer) => {
      const properties = (feature.properties ?? {}) as Record<string, unknown>;
      featureLayer.bindPopup(describe(properties));
    },
  }).addTo(map);

  const bounds = layer.getBounds();
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [24, 24] });
  }
  return layer;
}

function describe(properties: Record<string, unknown>): string {
  if (properties.site_id) {
    return `<b>Площадка</b><br/>${properties.name || properties.site_id}<br/>тип: ${properties.kind}`;
  }
  if (properties.kind) {
    return `<b>${properties.kind}</b><br/>${properties.name ?? ''}`;
  }
  const rows = [
    ['вылет', properties.sortie_id],
    ['борт', properties.vehicle_id],
    ['фаза', properties.phase],
    ['длина, м', properties.length_m],
    ['время, с', properties.duration_s],
    ['путевая, м/с', properties.ground_speed_mps],
    ['высота, м', properties.altitude_m],
  ];
  return rows
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value]) => `<b>${label}:</b> ${value}`)
    .join('<br/>');
}
