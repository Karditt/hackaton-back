/**
 * Подписи плана из его выгрузки GeoJSON: названия площадок и сценария.
 *
 * В расписании площадки и сценарий названы идентификаторами, а выгрузка
 * несёт слои сценария с именами — отсюда и берутся подписи для человека.
 */

import type { GeoJsonDocument } from '../api';

/** Название площадки по идентификатору — из слоя площадок выгрузки. */
export function siteNames(document: GeoJsonDocument): Map<string, string> {
  const names = new Map<string, string>();
  for (const feature of document.features) {
    const { kind, site_id: id, name } = feature.properties;
    if (kind === 'site' && typeof id === 'string') {
      names.set(id, typeof name === 'string' && name ? name : id);
    }
  }
  return names;
}

/** Название сценария, по которому посчитан план, — из слоя области съёмки. */
export function planTitle(document: GeoJsonDocument): string | null {
  const area = document.features.find((feature) => feature.properties.kind === 'survey_area');
  const name = area?.properties.name;
  return typeof name === 'string' && name ? name : null;
}
