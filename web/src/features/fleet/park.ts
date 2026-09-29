/**
 * Парк техники и каталог моделей — вкладка «Техника» (E10).
 *
 * Парк — борта, которые есть у пользователя: бортовое имя, модель,
 * установленная нагрузка, в строю ли борт. Флот расчёта набирается из бортов
 * парка, которые в строю, кнопкой в параметрах сценария.
 *
 * Каталог моделей — это `data/vehicles.yaml`, поверх которого пользователь
 * правит ТТХ (решение владельца, 2026-09-26): меняет числа модели каталога,
 * скрывает модель из работы, заводит свою модель. Правки хранятся отдельно от
 * каталога и только изменёнными полями, поэтому к исходным данным можно
 * вернуться — поле, модель или весь каталог разом. В расчёт правленые и свои
 * модели уходят записями `Scenario.vehicles`, с перечнем полей пользователя
 * в `user_fields`: план говорит, какие борта летят не по данным каталога.
 *
 * Хранение — на сервере (`GET/PUT /park`, `usePark`), копия — в браузере
 * (localStorage). Всё, что здесь есть, — перевод полей
 * формы и единиц показа; ни одной величины плана не считается.
 */

import type { FleetUnit, Payload, PayloadKind, Vehicle, VehicleType } from '../../api';
import type { FleetRow } from '../planning/scenarioOptions';

// ------------------------------------------------------------------- поля ТТХ

/** ТТХ модели, которые показываются и вводятся (поля `Vehicle` контракта). */
export type SpecKey =
  | 'cruise_speed_mps'
  | 'max_speed_mps'
  | 'min_speed_mps'
  | 'endurance_s'
  | 'max_wind_mps'
  | 'turn_radius_m'
  | 'min_altitude_agl_m'
  | 'max_altitude_agl_m'
  | 'max_range_m'
  | 'energy_wh';

export type SpecField = {
  key: SpecKey;
  label: string;
  /** Единица показа и ввода. */
  unit: string;
  /** Сколько единиц контракта в единице показа: минута — 60 секунд, км — 1000 м. */
  scale: number;
  /** Без этого числа модель в расчёте не описать. */
  required?: boolean;
  /** Поле только самолётного типа — и обязательное у него. */
  fixedWingOnly?: boolean;
};

export const SPEC_FIELDS: SpecField[] = [
  { key: 'cruise_speed_mps', label: 'Крейсерская скорость', unit: 'м/с', scale: 1, required: true },
  { key: 'max_speed_mps', label: 'Максимальная скорость', unit: 'м/с', scale: 1 },
  {
    key: 'min_speed_mps',
    label: 'Минимальная скорость',
    unit: 'м/с',
    scale: 1,
    fixedWingOnly: true,
  },
  {
    key: 'endurance_s',
    label: 'Продолжительность полёта',
    unit: 'мин',
    scale: 60,
    required: true,
  },
  { key: 'max_wind_mps', label: 'Допустимый ветер', unit: 'м/с', scale: 1, required: true },
  {
    key: 'turn_radius_m',
    label: 'Радиус разворота',
    unit: 'м',
    scale: 1,
    required: true,
    fixedWingOnly: true,
  },
  { key: 'min_altitude_agl_m', label: 'Мин. безопасная высота', unit: 'м', scale: 1 },
  { key: 'max_altitude_agl_m', label: 'Потолок над землёй', unit: 'м', scale: 1 },
  { key: 'max_range_m', label: 'Протяжённость маршрута', unit: 'км', scale: 1000 },
  { key: 'energy_wh', label: 'Энергия АКБ', unit: 'Вт·ч', scale: 1 },
];

/** Поля ТТХ, которые имеют смысл для типа борта. */
export function specFieldsFor(type: VehicleType): SpecField[] {
  return SPEC_FIELDS.filter((field) => !field.fixedWingOnly || type === 'fixed_wing');
}

/** Оптика нагрузки — те же четыре числа, которыми камеру описывает GeoScan Planner. */
export type PayloadSpecKey =
  'focal_length_mm' | 'sensor_width_mm' | 'sensor_height_mm' | 'image_width_px' | 'image_height_px';

export const PAYLOAD_SPEC_FIELDS: { key: PayloadSpecKey; label: string; unit: string }[] = [
  { key: 'focal_length_mm', label: 'Фокусное', unit: 'мм' },
  { key: 'sensor_width_mm', label: 'Матрица, ширина', unit: 'мм' },
  { key: 'sensor_height_mm', label: 'Матрица, высота', unit: 'мм' },
  { key: 'image_width_px', label: 'Кадр, ширина', unit: 'пикс' },
  { key: 'image_height_px', label: 'Кадр, высота', unit: 'пикс' },
];

/** Классы нагрузки контракта с подписями. Источник перечня — `core/schemas.py`. */
export const PAYLOAD_KINDS: [kind: PayloadKind, label: string][] = [
  ['rgb', 'Фотокамеры (RGB)'],
  ['multispectral', 'Мультиспектральные камеры'],
  ['ir', 'Тепловизоры (ИК)'],
  ['lidar', 'Лидары'],
  ['geophysical', 'Геофизические датчики'],
  ['inspection_rgb', 'Инспекционные зум-камеры'],
  ['gimbal', 'Подвесы'],
];

export const VEHICLE_TYPES: [type: VehicleType, label: string][] = [
  ['fixed_wing', 'самолёт'],
  ['multirotor', 'мультиротор'],
];

// --------------------------------------------------------------------- парк

/** Своя модель: ТТХ ввёл пользователь. Поля — строки в единицах показа. */
export type CustomModel = {
  id: string;
  name: string;
  type: VehicleType;
  specs: Record<SpecKey, string>;
  /** Нагрузки, которые модель несёт: из каталога и свои. */
  payloadIds: string[];
  /** Откуда числа — пишет пользователь. */
  source: string;
  /** Модель каталога, с которой сделана копия; null — с нуля. */
  basedOn: string | null;
};

/**
 * Правка модели каталога: только то, что пользователь изменил. Поля ТТХ —
 * строки в единицах показа; поля нет — значит, как в каталоге.
 */
export type CatalogEdit = {
  specs: Partial<Record<SpecKey, string>>;
  /** Состав нагрузок; нет — как в каталоге. */
  payloadIds?: string[];
};

/** Своя нагрузка: оптику ввёл пользователь. Поля — строки. */
export type CustomPayload = {
  id: string;
  name: string;
  kind: PayloadKind;
  specs: Record<PayloadSpecKey, string>;
  source: string;
};

/** Борт парка. */
export type ParkBoard = {
  id: string;
  /** Бортовое имя или номер. */
  label: string;
  /** Модель каталога или своя модель. */
  modelId: string;
  /** Нагрузки, которые есть у этого борта. */
  payloadIds: string[];
  /** В строю: борт можно брать во флот расчёта. */
  active: boolean;
  /** Комплекты АКБ — предел числа вылетов; пусто — не ограничено. */
  batterySets: string;
  note: string;
};

export type Park = {
  boards: ParkBoard[];
  models: CustomModel[];
  payloads: CustomPayload[];
  /** Правки моделей каталога по идентификатору модели. */
  edits: Record<string, CatalogEdit>;
  /** Модели каталога, выключенные из работы. Каталог при этом не меняется. */
  hidden: string[];
};

export const EMPTY_PARK: Park = { boards: [], models: [], payloads: [], edits: {}, hidden: [] };

// ----------------------------------------------------------- модели для показа

/** Модель, как её видит пользователь: каталожная с правками или своя. */
export type ModelView = {
  id: string;
  name: string;
  type: VehicleType;
  /** Запись каталога; null — своя модель. */
  base: Vehicle | null;
  /** Своя модель; null — модель каталога. */
  custom: CustomModel | null;
  /** Модель каталога выключена из работы. */
  hidden: boolean;
  /** ТТХ в единицах показа, с правками пользователя. */
  specs: Record<SpecKey, string>;
  /** Поля, заданные пользователем: у модели каталога — правленые, у своей — все. */
  edited: SpecKey[];
  /** Нагрузки модели, с правками. */
  payloadIds: string[];
  /** Состав нагрузок отличается от каталога. */
  payloadsEdited: boolean;
};

/** Число контракта в строку поля формы, в единицах показа. */
function specText(value: number | null | undefined, scale: number): string {
  if (value === null || value === undefined) return '';
  return String(Math.round((value / scale) * 1000) / 1000);
}

/** ТТХ модели каталога в единицах показа. */
export function catalogSpecs(vehicle: Vehicle): Record<SpecKey, string> {
  return Object.fromEntries(
    SPEC_FIELDS.map((field) => [field.key, specText(vehicle[field.key], field.scale)]),
  ) as Record<SpecKey, string>;
}

function sameList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item) => right.includes(item));
}

function catalogView(vehicle: Vehicle, park: Park): ModelView {
  const edit = park.edits[vehicle.id];
  const specs = { ...catalogSpecs(vehicle), ...(edit?.specs ?? {}) };
  const own = (vehicle.payloads ?? []).map((payload) => payload.id);
  const payloadIds = edit?.payloadIds ?? own;
  return {
    id: vehicle.id,
    name: vehicle.name,
    type: vehicle.type,
    base: vehicle,
    custom: null,
    hidden: park.hidden.includes(vehicle.id),
    specs,
    edited: Object.keys(edit?.specs ?? {}) as SpecKey[],
    payloadIds,
    payloadsEdited: !sameList(payloadIds, own),
  };
}

function customView(model: CustomModel): ModelView {
  return {
    id: model.id,
    name: model.name,
    type: model.type,
    base: null,
    custom: model,
    hidden: false,
    specs: model.specs,
    edited: specFieldsFor(model.type)
      .filter((field) => model.specs[field.key].trim() !== '')
      .map((field) => field.key),
    payloadIds: model.payloadIds,
    payloadsEdited: false,
  };
}

/** Все модели: сначала каталог с правками, потом свои. */
export function allModels(vehicles: Vehicle[], park: Park): ModelView[] {
  return [...vehicles.map((vehicle) => catalogView(vehicle, park)), ...park.models.map(customView)];
}

/** Правлена ли модель пользователем — числами или составом нагрузок. */
export function isEdited(model: ModelView): boolean {
  return model.base !== null && (model.edited.length > 0 || model.payloadsEdited);
}

/** Нагрузка для показа: каталожная или своя, с моделями, которые её несут. */
export type PayloadView =
  | {
      custom: false;
      id: string;
      name: string;
      kind: PayloadKind;
      payload: Payload;
      carriers: string[];
    }
  | {
      custom: true;
      id: string;
      name: string;
      kind: PayloadKind;
      payload: CustomPayload;
      carriers: string[];
    };

/**
 * Все нагрузки: каталожные (одна запись на идентификатор — одна и та же
 * камера стоит на нескольких бортах) и свои; у каждой — модели, которые её
 * несут с учётом правок.
 */
export function allPayloads(vehicles: Vehicle[], park: Park): PayloadView[] {
  const models = allModels(vehicles, park);
  const carriers = (id: string) =>
    models.filter((model) => model.payloadIds.includes(id)).map((model) => model.name);
  const catalog = new Map<string, PayloadView>();
  for (const vehicle of vehicles) {
    for (const payload of vehicle.payloads ?? []) {
      if (!catalog.has(payload.id)) {
        catalog.set(payload.id, {
          custom: false,
          id: payload.id,
          name: payload.name,
          kind: payload.kind,
          payload,
          carriers: carriers(payload.id),
        });
      }
    }
  }
  const own = park.payloads.map((payload): PayloadView => ({
    custom: true,
    id: payload.id,
    name: payload.name,
    kind: payload.kind,
    payload,
    carriers: carriers(payload.id),
  }));
  return [...catalog.values(), ...own];
}

// ------------------------------------------------------------ правка каталога

/**
 * Изменить поле ТТХ модели каталога. Значение, совпавшее с каталогом, правкой
 * не считается и убирается: «вернуть как было» — это просто ввести прежнее.
 */
export function editCatalogSpec(park: Park, vehicle: Vehicle, key: SpecKey, value: string): Park {
  const edit = park.edits[vehicle.id] ?? { specs: {} };
  const specs = { ...edit.specs };
  if (value.trim() === catalogSpecs(vehicle)[key]) delete specs[key];
  else specs[key] = value;
  return withEdit(park, vehicle.id, { ...edit, specs });
}

/** Изменить состав нагрузок модели каталога; совпал с каталогом — правки нет. */
export function editCatalogPayloads(park: Park, vehicle: Vehicle, payloadIds: string[]): Park {
  const edit = park.edits[vehicle.id] ?? { specs: {} };
  const own = (vehicle.payloads ?? []).map((payload) => payload.id);
  const next: CatalogEdit = sameList(payloadIds, own)
    ? { specs: edit.specs }
    : { ...edit, payloadIds };
  return withEdit(park, vehicle.id, next);
}

function withEdit(park: Park, id: string, edit: CatalogEdit): Park {
  const edits = { ...park.edits };
  if (Object.keys(edit.specs).length === 0 && edit.payloadIds === undefined) delete edits[id];
  else edits[id] = edit;
  return { ...park, edits };
}

/** Вернуть модели каталога её данные: убрать все правки. */
export function resetCatalogModel(park: Park, id: string): Park {
  const edits = { ...park.edits };
  delete edits[id];
  return { ...park, edits };
}

/** Вернуть исходный каталог: снять все правки и вернуть скрытые модели. */
export function resetCatalog(park: Park): Park {
  return { ...park, edits: {}, hidden: [] };
}

// -------------------------------------------------------------- создание

/** Уникальный идентификатор записи парка. */
function newId(prefix: string): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `${prefix}-${random}`;
}

/** Имя нового борта: «Геоскан 201 №3» — следующий номер среди бортов этой модели. */
function nextLabel(park: Park, model: ModelView): string {
  const same = park.boards.filter((board) => board.modelId === model.id).length;
  return `${model.name} №${same + 1}`;
}

/** Новый борт модели: в строю, с первой нагрузкой модели. */
export function newBoard(park: Park, model: ModelView): ParkBoard {
  return {
    id: newId('board'),
    label: nextLabel(park, model),
    modelId: model.id,
    payloadIds: model.payloadIds.slice(0, 1),
    active: true,
    batterySets: '',
    note: '',
  };
}

/** Своя модель с нуля. */
export function newModel(): CustomModel {
  return {
    id: newId('user-model'),
    name: 'Своя модель',
    type: 'multirotor',
    specs: Object.fromEntries(SPEC_FIELDS.map((field) => [field.key, ''])) as Record<
      SpecKey,
      string
    >,
    payloadIds: [],
    source: '',
    basedOn: null,
  };
}

/**
 * Своя модель — копия модели каталога с её правками. Дальше числа правит
 * пользователь, и источником модели становится он.
 */
export function modelFromView(model: ModelView): CustomModel {
  return {
    id: newId('user-model'),
    name: `${model.name} (своя)`,
    type: model.type,
    specs: { ...model.specs },
    payloadIds: [...model.payloadIds],
    source: `Копия «${model.name}» из каталога; правки — ввод пользователя`,
    basedOn: model.base?.id ?? null,
  };
}

/** Своя нагрузка с нуля. */
export function newPayload(kind: PayloadKind = 'rgb'): CustomPayload {
  return {
    id: newId('user-payload'),
    name: 'Своя нагрузка',
    kind,
    specs: {
      focal_length_mm: '',
      sensor_width_mm: '',
      sensor_height_mm: '',
      image_width_px: '',
      image_height_px: '',
    },
    source: '',
  };
}

// -------------------------------------------------------------- проверка

/** Строка поля в число; пусто — null, не число — NaN. */
function parseField(text: string): number | null {
  const trimmed = text.trim().replace(',', '.');
  return trimmed === '' ? null : Number(trimmed);
}

/**
 * Ошибки ввода ТТХ: обязательное пусто, не число, не больше нуля. Проверяется
 * только форма — противоречия между числами (крейсерская выше максимальной)
 * ядро находит на приёме сценария и называет.
 */
export function specErrors(model: ModelView): Partial<Record<SpecKey, string>> {
  const errors: Partial<Record<SpecKey, string>> = {};
  for (const field of specFieldsFor(model.type)) {
    const value = parseField(model.specs[field.key]);
    if (value === null) {
      if (field.required) errors[field.key] = 'обязательно';
    } else if (!Number.isFinite(value)) {
      errors[field.key] = 'не число';
    } else if (value <= 0) {
      errors[field.key] = 'должно быть больше нуля';
    }
  }
  return errors;
}

/** Ошибки ввода оптики своей нагрузки. */
export function payloadErrors(payload: CustomPayload): Partial<Record<PayloadSpecKey, string>> {
  const errors: Partial<Record<PayloadSpecKey, string>> = {};
  for (const field of PAYLOAD_SPEC_FIELDS) {
    const value = parseField(payload.specs[field.key]);
    if (value === null) continue;
    if (!Number.isFinite(value)) errors[field.key] = 'не число';
    else if (value <= 0) errors[field.key] = 'должно быть больше нуля';
  }
  return errors;
}

// ----------------------------------------------------- модели для расчёта

/** Своя нагрузка в форме контракта. */
function customPayload(payload: CustomPayload): Payload {
  const value = (key: PayloadSpecKey) => parseField(payload.specs[key]);
  const pixels = (key: PayloadSpecKey) => {
    const parsed = value(key);
    return parsed === null ? null : Math.round(parsed);
  };
  return {
    id: payload.id,
    name: payload.name,
    kind: payload.kind,
    source: payload.source.trim()
      ? `Ввод пользователя: ${payload.source.trim()}`
      : 'Ввод пользователя',
    focal_length_mm: value('focal_length_mm'),
    sensor_width_mm: value('sensor_width_mm'),
    sensor_height_mm: value('sensor_height_mm'),
    image_width_px: pixels('image_width_px'),
    image_height_px: pixels('image_height_px'),
    gaps: [],
    derived_fields: [],
  };
}

/**
 * Модель в форме контракта `Vehicle` — такой она уходит в расчёт.
 *
 * Модель каталога без правок возвращается как есть. Правленая — запись
 * каталога с числами пользователя: правленые поля перечислены в `user_fields`
 * и сняты из `derived_fields` и пробелов. Своя — целиком из полей формы.
 *
 * @returns null — в полях ошибки ввода: такую модель в расчёт не отдать
 */
export function toVehicle(model: ModelView, payloads: PayloadView[]): Vehicle | null {
  if (Object.keys(specErrors(model)).length > 0) return null;
  if (model.base !== null && !isEdited(model)) return model.base;

  const number = (key: SpecKey): number | null => {
    const parsed = parseField(model.specs[key]);
    const field = SPEC_FIELDS.find((item) => item.key === key);
    return parsed === null || field === undefined ? null : parsed * field.scale;
  };
  const payloadObjects = model.payloadIds
    .map((id) => payloads.find((item) => item.id === id))
    .filter((item): item is PayloadView => item !== undefined)
    .map((item) => (item.custom ? customPayload(item.payload) : item.payload));
  const fixedWing = model.type === 'fixed_wing';
  const specs = {
    cruise_speed_mps: number('cruise_speed_mps') ?? 0,
    max_speed_mps: number('max_speed_mps'),
    min_speed_mps: fixedWing ? number('min_speed_mps') : null,
    endurance_s: number('endurance_s') ?? 0,
    max_wind_mps: number('max_wind_mps') ?? 0,
    turn_radius_m: fixedWing ? (number('turn_radius_m') ?? 0) : 0,
    min_altitude_agl_m: number('min_altitude_agl_m'),
    max_altitude_agl_m: number('max_altitude_agl_m'),
    max_range_m: number('max_range_m'),
    energy_wh: number('energy_wh'),
  };
  const userFields: string[] = [...model.edited];
  if (model.payloadsEdited) userFields.push('payloads');

  if (model.base !== null) {
    const base = model.base;
    const edited = new Set<string>(model.edited);
    return {
      ...base,
      ...Object.fromEntries(model.edited.map((key) => [key, specs[key]])),
      payloads: payloadObjects,
      gaps: (base.gaps ?? []).filter((gap) => !edited.has(gap.split(':')[0])),
      derived_fields: (base.derived_fields ?? []).filter((field) => !edited.has(field)),
      user_fields: userFields,
    };
  }
  return {
    id: model.id,
    name: model.name,
    type: model.type,
    ...specs,
    // Отсечку заряда задаёт не пользователь: ядро ставит значение заказчика.
    battery_reserve_fraction: 0,
    takeoff_mode: null,
    landing_mode: null,
    payloads: payloadObjects,
    gaps: [],
    derived_fields: [],
    user_fields: userFields,
  };
}

/**
 * Записи `Scenario.vehicles` для флота: правленые и свои модели, которые во
 * флоте есть. Модели каталога без правок не отправляются — ядро возьмёт их
 * из каталога.
 *
 * @returns модели для расчёта и что помешало собрать остальные
 */
export function userVehicles(
  vehicleIds: string[],
  vehicles: Vehicle[],
  park: Park,
): { vehicles: Vehicle[]; problems: string[] } {
  const models = allModels(vehicles, park);
  const payloads = allPayloads(vehicles, park);
  const result: Vehicle[] = [];
  const problems: string[] = [];
  for (const id of [...new Set(vehicleIds)]) {
    const model = models.find((item) => item.id === id);
    if (!model || (model.base !== null && !isEdited(model))) continue;
    const vehicle = toVehicle(model, payloads);
    if (vehicle === null) {
      problems.push(`Модель «${model.name}»: ТТХ заполнены с ошибками — вкладка «Техника»`);
    } else {
      result.push(vehicle);
    }
  }
  return { vehicles: result, problems };
}

/**
 * Модели, из которых набирается флот: каталог с правками и свои, в форме
 * контракта. Скрытые модели каталога и свои модели с ошибками не входят.
 */
export function fleetCatalog(vehicles: Vehicle[], park: Park): Vehicle[] {
  const payloads = allPayloads(vehicles, park);
  return allModels(vehicles, park)
    .filter((model) => !model.hidden)
    .map((model) => toVehicle(model, payloads))
    .filter((vehicle): vehicle is Vehicle => vehicle !== null);
}

// --------------------------------------------------- связь с флотом расчёта

/**
 * Флот расчёта из бортов парка, которые в строю.
 *
 * Борта одной модели с одной нагрузкой и одним пределом АКБ сводятся в строку
 * с числом, как в контракте (`FleetUnit.count`). Нагрузка указывается, только
 * если у борта она одна; иначе её выберет модель съёмки из всех нагрузок
 * модели. Площадку выберет распределение. Не берутся борта скрытых моделей и
 * своих моделей с ошибками в ТТХ.
 */
export function parkToFleetRows(
  park: Park,
  vehicles: Vehicle[],
): { rows: FleetRow[]; skipped: ParkBoard[] } {
  const usable = new Set(fleetCatalog(vehicles, park).map((vehicle) => vehicle.id));
  const rows = new Map<string, FleetRow>();
  const skipped: ParkBoard[] = [];
  for (const board of park.boards) {
    if (!board.active) continue;
    if (!usable.has(board.modelId)) {
      skipped.push(board);
      continue;
    }
    const payload = board.payloadIds.length === 1 ? board.payloadIds[0] : '';
    const batteries = board.batterySets.trim();
    const key = `${board.modelId}|${payload}|${batteries}`;
    const row = rows.get(key);
    if (row) row.count = String(Number(row.count) + 1);
    else
      rows.set(key, {
        vehicle_id: board.modelId,
        count: '1',
        payload_id: payload,
        site_id: '',
        battery_sets: batteries,
      });
  }
  return { rows: [...rows.values()], skipped };
}

/**
 * Борта парка из флота сценария: строка с числом N даёт N бортов. Модели,
 * которых нет ни в каталоге, ни среди своих, пропускаются.
 *
 * Строка без нагрузки («выберет модель съёмки») даёт борт со всеми нагрузками
 * модели: тогда `parkToFleetRows` вернёт её тоже без нагрузки, и выбор
 * останется за моделью съёмки, как в сценарии.
 */
export function boardsFromFleet(park: Park, fleet: FleetUnit[], vehicles: Vehicle[]): ParkBoard[] {
  const models = allModels(vehicles, park);
  const added: ParkBoard[] = [];
  let current = park;
  for (const unit of fleet) {
    const model = models.find((item) => item.id === unit.vehicle_id);
    if (!model) continue;
    for (let index = 0; index < (unit.count ?? 1); index += 1) {
      const board = newBoard(current, model);
      board.payloadIds = unit.payload_id ? [unit.payload_id] : [...model.payloadIds];
      board.batterySets = unit.battery_sets ? String(unit.battery_sets) : '';
      added.push(board);
      current = { ...current, boards: [...current.boards, board] };
    }
  }
  return added;
}

// ------------------------------------------------------------- хранение

const STORAGE_KEY = 'geoscan.park.v1';

/** Прочитанное из хранилища в парк: хранилище браузера мог испортить кто угодно. */
export function asPark(value: unknown): Park | null {
  if (typeof value !== 'object' || value === null) return null;
  const park = value as Partial<Park>;
  if (!Array.isArray(park.boards) || !Array.isArray(park.models) || !Array.isArray(park.payloads))
    return null;
  // Парк, сохранённый до правок каталога, их не содержит.
  return {
    boards: park.boards,
    models: park.models,
    payloads: park.payloads,
    edits: typeof park.edits === 'object' && park.edits !== null ? park.edits : {},
    hidden: Array.isArray(park.hidden) ? park.hidden : [],
  };
}

/** Парк из хранилища браузера; нет или не читается — пустой. */
export function loadPark(): Park {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return EMPTY_PARK;
    return asPark(JSON.parse(raw)) ?? EMPTY_PARK;
  } catch {
    return EMPTY_PARK;
  }
}

/** Сохранить парк. Хранилище недоступно (приватное окно) — парк живёт до перезагрузки. */
export function savePark(park: Park): boolean {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(park));
    return true;
  } catch {
    return false;
  }
}
