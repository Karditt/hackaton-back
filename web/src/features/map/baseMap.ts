/**
 * Карта и подложка.
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
import { viewAltitudeControl } from './viewAltitude';

const OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>';

// Leaflet 1.9 по умолчанию ставит в префикс атрибуции флаг. Префикс заменён на
// простую ссылку на библиотеку. Атрибуцию OSM (OSM_ATTRIBUTION) убирать нельзя —
// её требует лицензия ODbL.
const LEAFLET_PREFIX =
  '<a href="https://leafletjs.com" title="JavaScript-библиотека интерактивных карт">Leaflet</a>';

/** Начальный вид до первого расчёта: Санкт-Петербург. */
const INITIAL_CENTER: L.LatLngExpression = [59.9, 30.3];
const INITIAL_ZOOM = 12;
const MAX_ZOOM = 18;

export function createMap(container: HTMLElement): L.Map {
  const map = L.map(container, { preferCanvas: true }).setView(INITIAL_CENTER, INITIAL_ZOOM);
  map.attributionControl.setPrefix(LEAFLET_PREFIX);
  L.tileLayer(import.meta.env.VITE_TILE_URL ?? OSM_URL, {
    attribution: OSM_ATTRIBUTION,
    maxZoom: MAX_ZOOM,
  }).addTo(map);
  // Нижние элементы угла Leaflet ставит друг над другом в обратном порядке:
  // линейка масштаба окажется внизу, высота обзора — над ней.
  L.control.scale({ imperial: false }).addTo(map);
  viewAltitudeControl('bottomleft').addTo(map);
  return map;
}
