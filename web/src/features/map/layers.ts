/**
 * Слои карты: что показывать и чей маршрут (E11).
 *
 * Слой — это вид объекта выгрузки, поле `kind` признака GeoJSON
 * (docs/export_format.md §4.2). Отбор идёт по признакам документа, который
 * вернул API: ни один объект здесь не строится и не пересчитывается.
 */

import type { GeoJsonDocument } from '../../api';

/** Группа слоёв под одной галочкой. */
export type LayerGroup = { id: string; label: string; kinds: string[] };

export const LAYER_GROUPS: LayerGroup[] = [
  { id: 'area', label: 'Область и пространство', kinds: ['survey_area', 'airspace_boundary'] },
  { id: 'no_fly_zone', label: 'Запретные зоны', kinds: ['no_fly_zone'] },
  { id: 'obstacle', label: 'Высотные препятствия', kinds: ['obstacle'] },
  { id: 'cell', label: 'Подобласти', kinds: ['survey_cell'] },
  { id: 'maneuver_zone', label: 'Зоны манёвра', kinds: ['maneuver_zone'] },
  { id: 'leg', label: 'Маршрут по фазам', kinds: ['leg'] },
  { id: 'keypoint', label: 'Ключевые точки', kinds: ['keypoint'] },
  { id: 'site', label: 'Площадки', kinds: ['site', 'recommended_site'] },
  { id: 'violation', label: 'Нарушения', kinds: ['violation'] },
];

/** Все виды объектов, которые интерфейс знает и умеет прятать. */
const KNOWN_KINDS = new Set(LAYER_GROUPS.flatMap((group) => group.kinds));

/** Отбор объектов карты: какие слои показаны и чей маршрут. */
export type MapFilter = {
  /** Идентификаторы включённых групп. */
  groups: string[];
  /** Борт, чьи объекты показаны; пустая строка — все борта. */
  board: string;
};

/** Отбор по умолчанию: показано всё. */
export function allLayers(): MapFilter {
  return { groups: LAYER_GROUPS.map((group) => group.id), board: '' };
}

/**
 * Документ карты после отбора.
 *
 * Вид объекта, о котором интерфейс не знает, остаётся видимым: новый слой
 * выгрузки не должен пропадать с карты молча. Объект без `board_id` —
 * общий (область, зоны, препятствия, площадки), он виден при любом борте.
 */
export function filterMission(document: GeoJsonDocument, filter: MapFilter): GeoJsonDocument {
  const shown = new Set(
    LAYER_GROUPS.filter((group) => filter.groups.includes(group.id)).flatMap(
      (group) => group.kinds,
    ),
  );
  const features = document.features.filter((feature) => {
    const kind = String(feature.properties.kind ?? 'leg');
    if (KNOWN_KINDS.has(kind) && !shown.has(kind)) return false;
    if (filter.board === '') return true;
    const board = feature.properties.board_id;
    return board === undefined || board === null || board === filter.board;
  });
  return { ...document, features };
}
