/**
 * Подписи признаков. Leaflet принимает их HTML-строкой, поэтому всё, что
 * пришло из сценария (имена площадок, зон, препятствий), экранируется.
 */

import { escapeHtml } from '../../shared/format';

type Properties = Record<string, unknown>;

const LEG_ROWS: [label: string, key: string][] = [
  ['вылет', 'sortie_id'],
  ['борт', 'board_id'],
  ['фаза', 'phase'],
  ['подобласть', 'cell_id'],
  ['длина, м', 'length_m'],
  ['время, с', 'duration_s'],
  ['путевая, м/с', 'ground_speed_mps'],
  ['высота AGL, м', 'altitude_m'],
  ['начало', 'start_time'],
];

const KIND_TITLE: Record<string, string> = {
  survey_area: 'Область съёмки',
  survey_cell: 'Подобласть',
  maneuver_zone: 'Зона манёвра самолёта',
  no_fly_zone: 'Запретная зона',
  obstacle: 'Высотное препятствие',
  airspace_boundary: 'Граница воздушного пространства',
  site: 'Площадка',
  violation: 'Нарушение',
  recommended_site: 'Предлагаемая площадка',
};

const KEYPOINT_ROWS: [label: string, key: string][] = [
  ['борт', 'board_id'],
  ['вылет', 'sortie_id'],
  ['высота AGL, м', 'altitude_m'],
  ['время', 'time'],
  ['от начала, с', 'time_s'],
];

function rows(properties: Properties, keys: [string, string][]): string {
  return keys
    .filter(([, key]) => properties[key] !== undefined && properties[key] !== null)
    .map(([label, key]) =>
      label ? `<b>${label}:</b> ${escapeHtml(properties[key])}` : escapeHtml(properties[key]),
    )
    .join('<br/>');
}

export function describeFeature(properties: Properties): string {
  const kind = String(properties.kind ?? 'leg');
  if (kind === 'leg') {
    return rows(properties, LEG_ROWS);
  }
  if (kind === 'keypoint') {
    return `<b>${escapeHtml(properties.title ?? 'Ключевая точка')}</b><br/>${rows(properties, KEYPOINT_ROWS)}`;
  }
  const title = `<b>${escapeHtml(KIND_TITLE[kind] ?? kind)}</b>`;
  const details = rows(properties, [
    ['имя', 'name'],
    ['тип', 'site_kind'],
    ['высота, м', 'height_m'],
    ['от, м', 'min_alt_m'],
    ['до, м', 'max_alt_m'],
    ['действует с', 'active_from'],
    ['действует до', 'active_to'],
    ['вылет', 'sortie_id'],
    ['борт', 'board_id'],
    ['', 'message'],
  ]);
  return details ? `${title}<br/>${details}` : title;
}
