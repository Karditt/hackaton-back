/**
 * Подписи объектов карты. Leaflet принимает их HTML-строкой, поэтому всё, что
 * пришло из сценария (имена площадок, зон, препятствий), экранируется.
 *
 * Внутренние идентификаторы (борт, вылет) переводятся в то, что видит
 * человек: подпись борта из сводки выгрузки и номер вылета.
 */

import type { BoardSummary } from '../../api';
import { decimal, duration, escapeHtml, hours, length, utcMoment } from '../../shared/format';

type Properties = Record<string, unknown>;

/** Что нужно подписи, кроме свойств самого объекта. */
export type PopupContext = {
  /** Борта плана — подпись «Геоскан 201 #2» по `board_id`. */
  boards: BoardSummary[];
};

/** Фазы полёта — как в легенде карты. */
export const PHASE_LABELS: Record<string, string> = {
  takeoff: 'Взлёт',
  climb: 'Набор высоты',
  transit: 'Перелёт',
  strip: 'Съёмка (галс)',
  turn: 'Разворот',
  hold: 'Ожидание в воздухе',
  descent: 'Снижение',
  landing: 'Посадка',
};

const KIND_TITLE: Record<string, string> = {
  survey_area: 'Область съёмки',
  survey_cell: 'Подобласть съёмки',
  maneuver_zone: 'Зона манёвра самолёта',
  no_fly_zone: 'Запретная зона',
  obstacle: 'Высотное препятствие',
  airspace_boundary: 'Граница воздушного пространства',
  site: 'Площадка',
  violation: 'Нарушение',
  recommended_site: 'Предлагаемая площадка',
};

const SITE_KIND: Record<string, string> = {
  primary: 'основная',
  reserve: 'резервная',
};

/** Число из свойства; не число — null. */
function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Строки «подпись: значение»; пустые значения пропускаются. */
function rows(items: [label: string, value: string | null | undefined][]): string {
  return items
    .filter((item): item is [string, string] => item[1] !== null && item[1] !== undefined)
    .map(([label, value]) => `<b>${label}:</b> ${escapeHtml(value)}`)
    .join('<br/>');
}

function boardLabel(properties: Properties, context: PopupContext): string | null {
  const id = properties.board_id;
  if (id === undefined || id === null) return null;
  return context.boards.find((board) => board.board_id === id)?.label ?? String(id);
}

function sortieNumber(properties: Properties): string | null {
  const index = numberOf(properties.trip_index);
  return index === null ? null : `№ ${index + 1}`;
}

/** Момент: время UTC, если известно начало работ, иначе — от начала работ. */
function moment(iso: unknown, offsetS: unknown): string | null {
  if (typeof iso === 'string' && iso) return utcMoment(iso);
  const offset = numberOf(offsetS);
  return offset === null ? null : `через ${hours(offset)} от начала работ`;
}

function metres(value: unknown): string | null {
  const number = numberOf(value);
  return number === null ? null : `${decimal(number, 0)} м`;
}

function title(text: string): string {
  return `<b>${escapeHtml(text)}</b>`;
}

function join(head: string, body: string): string {
  return body ? `${head}<br/>${body}` : head;
}

export function describeFeature(properties: Properties, context: PopupContext): string {
  const kind = String(properties.kind ?? 'leg');

  if (kind === 'leg') {
    const phase = String(properties.phase ?? '');
    const lengthM = numberOf(properties.length_m);
    const durationS = numberOf(properties.duration_s);
    const speed = numberOf(properties.ground_speed_mps);
    return join(
      title(PHASE_LABELS[phase] ?? 'Участок маршрута'),
      rows([
        ['борт', boardLabel(properties, context)],
        ['вылет', sortieNumber(properties)],
        ['подобласть', properties.cell_id ? String(properties.cell_id) : null],
        ['длина', lengthM === null ? null : length(lengthM)],
        ['время', durationS === null ? null : duration(durationS)],
        ['путевая скорость', speed === null ? null : `${decimal(speed, 1)} м/с`],
        ['высота над землёй', metres(properties.altitude_m)],
        ['начало', moment(properties.start_time, properties.start_s)],
      ]),
    );
  }

  if (kind === 'keypoint') {
    return join(
      title(String(properties.title ?? 'Ключевая точка')),
      rows([
        ['борт', boardLabel(properties, context)],
        ['вылет', sortieNumber(properties)],
        ['высота над землёй', metres(properties.altitude_m)],
        ['время', moment(properties.time, properties.time_s)],
      ]),
    );
  }

  const head = title(KIND_TITLE[kind] ?? 'Объект');
  if (kind === 'survey_cell') {
    const area = numberOf(properties.area_m2);
    return join(
      head,
      rows([
        ['номер', properties.cell_id ? String(properties.cell_id) : null],
        ['площадь', area === null ? null : `${decimal(area / 10000, 1)} га`],
        ['борт', boardLabel(properties, context)],
      ]),
    );
  }
  if (kind === 'maneuver_zone') {
    return join(
      head,
      rows([
        ['подобласть', properties.cell_id ? String(properties.cell_id) : null],
        ['борт', boardLabel(properties, context)],
      ]),
    );
  }
  if (kind === 'violation' || kind === 'recommended_site') {
    return join(head, properties.message ? escapeHtml(properties.message) : '');
  }
  const kindName = typeof properties.site_kind === 'string' ? properties.site_kind : '';
  return join(
    head,
    rows([
      ['название', properties.name ? String(properties.name) : null],
      ['назначение', kindName ? (SITE_KIND[kindName] ?? kindName) : null],
      ['высота', metres(properties.height_m)],
      ['высоты от', metres(properties.min_alt_m)],
      ['высоты до', metres(properties.max_alt_m)],
      [
        'действует с',
        typeof properties.active_from === 'string' ? utcMoment(properties.active_from) : null,
      ],
      [
        'действует до',
        typeof properties.active_to === 'string' ? utcMoment(properties.active_to) : null,
      ],
    ]),
  );
}
