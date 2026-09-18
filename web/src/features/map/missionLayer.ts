import L from 'leaflet';
import type { GeoJsonDocument } from '../../api';
import { DEFAULT_MARKER, KIND_STYLES, POINT_MARKERS, legStyle } from './missionStyles';
import { describeFeature } from './popup';

const FIT_PADDING: L.PointTuple = [24, 24];

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
  const layer = L.geoJSON(document as never, {
    style: (feature) => {
      const properties = (feature?.properties ?? {}) as Record<string, string | undefined>;
      const kind = properties.kind;
      return (kind && kind !== 'leg' && KIND_STYLES[kind]) || legStyle(properties.phase);
    },
    pointToLayer: (feature, latlng) => {
      const kind = (feature.properties as Record<string, string | undefined>)?.kind ?? '';
      return L.circleMarker(latlng, POINT_MARKERS[kind] ?? DEFAULT_MARKER);
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
