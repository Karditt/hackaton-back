/**
 * Виды исходных данных — разделы менеджера файлов (E10).
 *
 * Заказчик готовит геометрию в ГИС и присылает файлами: область съёмки, зоны
 * ограничений и высотные препятствия — тремя слоями KML
 * (docs/input_formats.md), параметры задания — JSON. Площадки и граница
 * воздушного пространства живут в параметрах задания. Вид связан с группой
 * слоёв карты (`features/map/layers.ts`), если его можно показать на карте, и
 * с исходным файлом сценария (`GET /scenarios/{id}/files`).
 */

import type { ScenarioFile } from '../../api';

/** Идентификатор вида исходных данных. */
export type DataKindId =
  'area' | 'no_fly_zone' | 'obstacle' | 'airspace' | 'site' | 'params' | 'scenario';

export type DataKind = {
  id: DataKindId;
  label: string;
  /** Что это за данные — одной строкой. */
  hint: string;
  /** Группа слоёв карты; `null` — вид на карте не рисуется. */
  layerGroup: string | null;
  /** Ключ счётчика в свойствах слоёв сценария (`counts`); `null` — не считается. */
  countKey: string | null;
  /** Формы слова после числа: 1, 2–4, 5–20. */
  countForms: [string, string, string];
  /** Исходный файл сценария, в котором лежат эти данные. */
  source: ScenarioFile['kind'];
  /** Фильтр выбора файла. */
  accept: string;
  /** Форматы для подписи. */
  formats: string;
};

export const DATA_KINDS: DataKind[] = [
  {
    id: 'area',
    label: 'Область съёмки',
    hint: 'задание: что снять',
    layerGroup: 'area',
    countKey: 'survey_area',
    countForms: ['полигон', 'полигона', 'полигонов'],
    source: 'area',
    accept: '.kml',
    formats: 'KML',
  },
  {
    id: 'no_fly_zone',
    label: 'Запретные зоны',
    hint: 'зоны ограничений с высотами и временем действия',
    layerGroup: 'no_fly_zone',
    countKey: 'no_fly_zone',
    countForms: ['зона', 'зоны', 'зон'],
    source: 'zones',
    accept: '.kml',
    formats: 'KML',
  },
  {
    id: 'obstacle',
    label: 'Высотные препятствия',
    hint: 'мачты, трубы, линии электропередачи',
    layerGroup: 'obstacle',
    countKey: 'obstacle',
    countForms: ['объект', 'объекта', 'объектов'],
    source: 'obstacles',
    accept: '.kml',
    formats: 'KML',
  },
  {
    id: 'airspace',
    label: 'Граница воздушного пространства',
    hint: 'за неё маршрут не выходит',
    layerGroup: 'airspace',
    countKey: 'airspace_boundary',
    countForms: ['контур', 'контура', 'контуров'],
    source: 'params',
    accept: '.json',
    formats: 'JSON параметров',
  },
  {
    id: 'site',
    label: 'Площадки взлёта и посадки',
    hint: 'штатные и резервные',
    layerGroup: 'site',
    countKey: 'site',
    countForms: ['площадка', 'площадки', 'площадок'],
    source: 'params',
    accept: '.json',
    formats: 'JSON параметров',
  },
  {
    id: 'params',
    label: 'Параметры задания',
    hint: 'GSD, тип съёмки, флот, площадки, сроки',
    layerGroup: null,
    countKey: null,
    countForms: ['', '', ''],
    source: 'params',
    accept: '.json',
    formats: 'JSON',
  },
  {
    id: 'scenario',
    label: 'Сценарий целиком',
    hint: 'собран из файлов выше, по контракту',
    layerGroup: null,
    countKey: null,
    countForms: ['', '', ''],
    source: 'scenario',
    accept: '.json',
    formats: 'JSON',
  },
];
