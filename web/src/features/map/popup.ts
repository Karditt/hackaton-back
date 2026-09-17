/**
 * Подписи признаков плана. Leaflet принимает их HTML-строкой, поэтому всё,
 * что пришло из сценария (имена площадок и зон), экранируется.
 */

import { escapeHtml } from '../../shared/format';

type Properties = Record<string, unknown>;

const LEG_ROWS: [label: string, key: string][] = [
  ['вылет', 'sortie_id'],
  ['борт', 'vehicle_id'],
  ['фаза', 'phase'],
  ['длина, м', 'length_m'],
  ['время, с', 'duration_s'],
  ['путевая, м/с', 'ground_speed_mps'],
  ['высота, м', 'altitude_m'],
];

export function describeFeature(properties: Properties): string {
  if (properties.site_id) {
    const name = properties.name || properties.site_id;
    return `<b>Площадка</b><br/>${escapeHtml(name)}<br/>тип: ${escapeHtml(properties.kind)}`;
  }
  if (properties.kind) {
    return `<b>${escapeHtml(properties.kind)}</b><br/>${escapeHtml(properties.name ?? '')}`;
  }
  return LEG_ROWS.filter(([, key]) => properties[key] !== undefined && properties[key] !== null)
    .map(([label, key]) => `<b>${label}:</b> ${escapeHtml(properties[key])}`)
    .join('<br/>');
}
