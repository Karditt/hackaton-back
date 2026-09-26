/**
 * Слои карты: что показывать и чей маршрут (E11).
 *
 * Слой — это вид объекта выгрузки, поле `kind` признака GeoJSON
 * (docs/export_format.md §4.2). Отбор идёт по признакам документа, который
 * вернул API: ни один объект здесь не строится и не пересчитывается.
 *
 * Слои двух родов. Исходные данные — то, что пришло файлами сценария:
 * область, зоны, препятствия, площадки; их показ переключается в менеджере
 * данных и виден до расчёта. Результат расчёта — подобласти, маршрут, точки,
 * нарушения; их переключатели появляются вместе с планом.
 */

import type { GeoJsonDocument } from '../../api';

/** Род слоя: исходные данные сценария или результат расчёта. */
export type LayerScope = 'input' | 'plan';

/** Группа слоёв под одной галочкой. */
export type LayerGroup = { id: string; label: string; kinds: string[]; scope: LayerScope };

export const LAYER_GROUPS: LayerGroup[] = [
  { id: 'area', label: 'Область съёмки', kinds: ['survey_area'], scope: 'input' },
  { id: 'no_fly_zone', label: 'Запретные зоны', kinds: ['no_fly_zone'], scope: 'input' },
  { id: 'obstacle', label: 'Высотные препятствия', kinds: ['obstacle'], scope: 'input' },
  { id: 'airspace', label: 'Граница пространства', kinds: ['airspace_boundary'], scope: 'input' },
  { id: 'site', label: 'Площадки', kinds: ['site'], scope: 'input' },
  { id: 'cell', label: 'Подобласти', kinds: ['survey_cell'], scope: 'plan' },
  { id: 'maneuver_zone', label: 'Зоны манёвра', kinds: ['maneuver_zone'], scope: 'plan' },
  { id: 'leg', label: 'Маршрут по фазам', kinds: ['leg'], scope: 'plan' },
  { id: 'keypoint', label: 'Ключевые точки', kinds: ['keypoint'], scope: 'plan' },
  { id: 'violation', label: 'Нарушения', kinds: ['violation'], scope: 'plan' },
  {
    id: 'recommended_site',
    label: 'Предлагаемые площадки',
    kinds: ['recommended_site'],
    scope: 'plan',
  },
];

/** Группы одного рода — в порядке списка. */
export function groupsOf(scope: LayerScope): LayerGroup[] {
  return LAYER_GROUPS.filter((group) => group.scope === scope);
}

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
 * Показать все слои расчёта и все борта, не трогая исходные данные: их
 * пользователь мог спрятать в менеджере данных, и новый расчёт не должен
 * включать их обратно.
 */
export function resetPlanLayers(filter: MapFilter): MapFilter {
  const inputShown = filter.groups.filter((id) =>
    groupsOf('input').some((group) => group.id === id),
  );
  return { groups: [...inputShown, ...groupsOf('plan').map((group) => group.id)], board: '' };
}

/** Включить или выключить одну группу. */
export function toggleGroup(filter: MapFilter, id: string, shown: boolean): MapFilter {
  const rest = filter.groups.filter((item) => item !== id);
  return { ...filter, groups: shown ? [...rest, id] : rest };
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
