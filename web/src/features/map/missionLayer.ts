import L from 'leaflet';
import type { GeoJsonDocument } from '../../api';
import { AREA_STYLES, SITE_MARKER, legStyle } from './missionStyles';
import { describeFeature } from './popup';

const FIT_PADDING: L.PointTuple = [24, 24];

/**
 * Отрисовать документ GeoJSON, полученный от API, и перевести карту к нему.
 *
 * @param map карта Leaflet
 * @param document выгрузка плана в WGS84
 * @returns слой, который вызывающий код снимет перед следующей отрисовкой
 */
export function drawMission(map: L.Map, document: GeoJsonDocument): L.GeoJSON {
  const layer = L.geoJSON(document as never, {
    style: (feature) => {
      const properties = (feature?.properties ?? {}) as Record<string, string | undefined>;
      const kind = properties.kind;
      return (kind && AREA_STYLES[kind]) || legStyle(properties.phase);
    },
    pointToLayer: (_feature, latlng) => L.circleMarker(latlng, SITE_MARKER),
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
