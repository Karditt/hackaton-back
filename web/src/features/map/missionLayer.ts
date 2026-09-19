import L from 'leaflet';
import type { GeoJsonDocument, GeoJsonFeature } from '../../api';
import {
  DEFAULT_MARKER,
  KEYPOINT_FILL,
  KIND_STYLES,
  POINT_MARKERS,
  legStyle,
} from './missionStyles';
import { describeFeature } from './popup';

const FIT_PADDING: L.PointTuple = [24, 24];

/**
 * Порядок отрисовки по полю `kind`: меньше — ниже. Leaflet рисует объекты в
 * порядке добавления, и верхний перехватывает щелчок. В файле слои сценария
 * идут последними (docs/export_format.md §4.2) и накрыли бы маршрут, поэтому
 * внизу — площадные слои сценария, выше — подобласти, маршрут и ключевые
 * точки. Площадки — на самом верху: на них сходятся точки взлёта и посадки
 * всех вылетов, и площадка не должна под ними теряться.
 */
const DRAW_ORDER: Record<string, number> = {
  survey_area: 0,
  airspace_boundary: 0,
  no_fly_zone: 1,
  obstacle: 2,
  survey_cell: 3,
  maneuver_zone: 4,
  leg: 5,
  violation: 6,
  keypoint: 7,
  site: 8,
  recommended_site: 8,
};

function drawRank(feature: GeoJsonFeature): number {
  return DRAW_ORDER[String(feature.properties.kind ?? 'leg')] ?? DRAW_ORDER.leg;
}

/**
 * Отрисовать документ GeoJSON, полученный от API, и перевести карту к нему.
 * Это полётное задание либо отчёт о невыполнимости — с местами нарушений и
 * предлагаемыми площадками.
 *
 * @param map карта Leaflet
 * @param document выгрузка в WGS84
 * @returns слой, который вызывающий код снимет перед следующей отрисовкой
 */
export function drawMission(map: L.Map, document: GeoJsonDocument): L.GeoJSON {
  // Сортировка устойчива: внутри одного слоя порядок файла сохраняется.
  const features = [...document.features].sort((a, b) => drawRank(a) - drawRank(b));
  const ordered: GeoJsonDocument = { ...document, features };
  const layer = L.geoJSON(ordered as never, {
    style: (feature) => {
      const properties = (feature?.properties ?? {}) as Record<string, string | undefined>;
      const kind = properties.kind;
      return (kind && kind !== 'leg' && KIND_STYLES[kind]) || legStyle(properties.phase);
    },
    pointToLayer: (feature, latlng) => {
      const properties = (feature.properties ?? {}) as Record<string, string | undefined>;
      const kind = properties.kind ?? '';
      const marker = POINT_MARKERS[kind] ?? DEFAULT_MARKER;
      const fill = kind === 'keypoint' ? KEYPOINT_FILL[properties.keypoint ?? ''] : undefined;
      return L.circleMarker(latlng, fill ? { ...marker, fillColor: fill } : marker);
    },
    onEachFeature: (feature, featureLayer) => {
      featureLayer.bindPopup(describeFeature(feature.properties ?? {}));
    },
  }).addTo(map);

  const bounds = layer.getBounds();
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: FIT_PADDING });
  }
  return layer;
}
