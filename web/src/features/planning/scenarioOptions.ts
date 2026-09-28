/**
 * Редактируемые параметры сценария (E10).
 *
 * Заказчик исключил ввод и редактирование геометрии (№8, №12), поэтому
 * область, зоны, препятствия и площадки берутся из выбранного сценария как
 * есть. Правится только то, что заказчик называет параметрами задания: GSD и
 * тип съёмки, состав флота, ветер, коридор высот, время, высота Солнца.
 *
 * Здесь нет ни одного расчёта плана: значения формы переводятся в поля
 * контракта `core/schemas.py` и отправляются серверу, а он их проверяет
 * (`POST /scenarios` → 422 с причиной). Единственная арифметика — перевод
 * единиц показа (часы и минуты в секунды), потому что дедлайн в секундах
 * человеку вводить нечем.
 *
 * Поля формы хранятся строками: поле, очищенное пользователем, — это
 * «не задано», а не 0. Числа получаются при отправке, и ошибка разбора
 * называет поле.
 */

import type { FleetUnit, Scenario, SurveyType, Vehicle } from '../../api';

/** Суффикс производного сценария: правка панели не затирает исходный файл. */
const CUSTOM_SUFFIX = '--custom';

/** Секунд в часе и в минуте — для перевода единиц показа. */
const SECONDS_IN_HOUR = 3600;
const SECONDS_IN_MINUTE = 60;

/** Предел флота — заказчик, №2, №43 (`assumptions.MAX_FLEET_VEHICLES`). */
export const MAX_FLEET_VEHICLES = 15;

/** Типы съёмки контракта с подписями. Источник перечня — `core/schemas.py`. */
export const SURVEY_TYPES: [type: SurveyType, label: string][] = [
  ['rgb', 'Фотосъёмка (RGB)'],
  ['multispectral', 'Мультиспектральная'],
  ['ir', 'Инфракрасная'],
  ['lidar', 'Лазерное сканирование'],
  ['geophysical', 'Геофизическая'],
];

/** Строка состава флота в виде полей формы. */
export type FleetRow = {
  vehicle_id: string;
  count: string;
  /** Нагрузка; пусто — выберет модель съёмки (E2). */
  payload_id: string;
  /** Площадка приписки; пусто — выберет распределение (E6). */
  site_id: string;
  /** Комплекты АКБ — предел числа вылетов борта; пусто — не ограничено. */
  battery_sets: string;
};

/** Параметры сценария, доступные правке в вебе. */
export type ScenarioOptions = {
  gsd_m: string;
  survey_type: SurveyType;
  wind_speed_mps: string;
  /** Метеорологическая конвенция — ОТКУДА дует (инвариант 4). */
  wind_direction_deg: string;
  max_altitude_agl_m: string;
  /** Начало работ, UTC, в виде `YYYY-MM-DDTHH:mm`; пусто — время относительное. */
  start_time: string;
  /** Дедлайн от начала работ, часы; пусто — без дедлайна. */
  deadline_h: string;
  /** Межвылетная подготовка, минуты; пусто — допущение ядра (№32). */
  turnaround_min: string;
  /** Снимать только при Солнце не ниже, градусы; пусто — время суток не учитывается. */
  min_sun_elevation_deg: string;
  fleet: FleetRow[];
};

/** Число из поля формы или `null`, если поле пустое. */
function numberText(value: number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value);
}

/** Строка формы в число; пустая строка — `null`. */
function parseOptional(text: string, label: string): number | null {
  const trimmed = text.trim().replace(',', '.');
  if (trimmed === '') return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    throw new Error(`Поле «${label}»: ${text.trim()} — не число`);
  }
  return value;
}

/** Строка формы в число; пустая строка — ошибка с именем поля. */
function parseRequired(text: string, label: string): number {
  const value = parseOptional(text, label);
  if (value === null) {
    throw new Error(`Поле «${label}» обязательно`);
  }
  return value;
}

/** Параметры выбранного сценария в виде полей формы. */
export function optionsOf(scenario: Scenario): ScenarioOptions {
  return {
    gsd_m: numberText(scenario.quality.gsd_m),
    survey_type: scenario.survey_type ?? 'rgb',
    wind_speed_mps: numberText(scenario.wind?.speed_mps ?? 0),
    wind_direction_deg: numberText(scenario.wind?.direction_deg ?? 0),
    max_altitude_agl_m: numberText(scenario.altitude_corridor.max_altitude_agl_m),
    // Ввод datetime-local принимает минуты, поэтому секунды отбрасываются.
    start_time: scenario.start_time ? scenario.start_time.slice(0, 16) : '',
    deadline_h:
      scenario.deadline_s === null || scenario.deadline_s === undefined
        ? ''
        : String(scenario.deadline_s / SECONDS_IN_HOUR),
    turnaround_min:
      scenario.turnaround_s === null || scenario.turnaround_s === undefined
        ? ''
        : String(scenario.turnaround_s / SECONDS_IN_MINUTE),
    min_sun_elevation_deg: numberText(scenario.min_sun_elevation_deg),
    fleet: (scenario.fleet ?? []).map(fleetRow),
  };
}

/** Строка флота сценария в виде полей формы. */
export function fleetRow(unit: FleetUnit): FleetRow {
  return {
    vehicle_id: unit.vehicle_id,
    count: numberText(unit.count ?? 1),
    payload_id: unit.payload_id ?? '',
    site_id: unit.site_id ?? '',
    battery_sets: numberText(unit.battery_sets),
  };
}

/** Новая строка флота: борт есть, остальное — по умолчанию. */
export function newFleetRow(vehicleId: string): FleetRow {
  return { vehicle_id: vehicleId, count: '1', payload_id: '', site_id: '', battery_sets: '' };
}

/** Сколько бортов набрано: `count` суммируется по строкам, как в контракте. */
export function fleetSize(rows: FleetRow[]): number {
  return rows.reduce((total, row) => total + (parseOptional(row.count, 'Число бортов') ?? 0), 0);
}

/**
 * Идентификатор производного сценария.
 *
 * Суффикс один и тот же, поэтому повторный расчёт с правками перезаписывает
 * тот же файл хранилища, а не оставляет след после каждой правки. Правка
 * производного сценария остаётся в нём же.
 */
export function customScenarioId(baseId: string): string {
  return baseId.endsWith(CUSTOM_SUFFIX) ? baseId : `${baseId}${CUSTOM_SUFFIX}`;
}

/** Правлены ли параметры относительно сохранённого сценария. */
export function isEdited(scenario: Scenario, options: ScenarioOptions): boolean {
  // Оба объекта собраны `optionsOf` и правились через spread, поэтому порядок
  // ключей одинаков и сравнение текстом корректно.
  return JSON.stringify(options) !== JSON.stringify(optionsOf(scenario));
}

/** Значение без пустых полей и с упорядоченными ключами — для сравнения текстом. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, item]) => item !== null && item !== undefined)
        .filter(([, item]) => !(Array.isArray(item) && item.length === 0))
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonical(item)]),
    );
  }
  return value;
}

/**
 * Совпадают ли ТТХ пользователя с теми, что уже лежат в сценарии.
 *
 * Сервер хранит модели с полями по умолчанию, а вкладка «Техника» собирает
 * их без пустых — поэтому сравниваются канонические формы.
 */
export function sameVehicles(scenario: Scenario, vehicles: Vehicle[]): boolean {
  const byId = (items: Vehicle[]) => [...items].sort((a, b) => a.id.localeCompare(b.id));
  return (
    JSON.stringify(canonical(byId(scenario.vehicles ?? []))) ===
    JSON.stringify(canonical(byId(vehicles)))
  );
}

/**
 * Сценарий с применёнными правками — то, что уходит в `POST /scenarios`.
 *
 * Геометрия, зоны, препятствия и площадки переносятся из исходного сценария
 * без изменений. ТТХ пользователя (вкладка «Техника») заменяют те, что были
 * в сценарии: источник правок в браузере — парк.
 *
 * @param vehicles правленые и свои модели флота — `Scenario.vehicles`
 * @throws Error поле формы не разбирается в число — с именем поля.
 */
export function applyOptions(
  scenario: Scenario,
  options: ScenarioOptions,
  vehicles: Vehicle[] = [],
): Scenario {
  const units: FleetUnit[] = options.fleet.map((row, index) => {
    const position = `борт ${index + 1}`;
    return {
      vehicle_id: row.vehicle_id,
      count: parseRequired(row.count, `Число бортов (${position})`),
      payload_id: row.payload_id === '' ? null : row.payload_id,
      site_id: row.site_id === '' ? null : row.site_id,
      battery_sets: parseOptional(row.battery_sets, `Комплекты АКБ (${position})`),
    };
  });
  const [first, ...rest] = units;
  if (first === undefined) {
    throw new Error('Во флоте нет ни одного борта');
  }

  const deadlineHours = parseOptional(options.deadline_h, 'Дедлайн');
  const turnaroundMinutes = parseOptional(options.turnaround_min, 'Межвылетная подготовка');
  const sunDegrees = parseOptional(options.min_sun_elevation_deg, 'Солнце не ниже');

  return {
    ...scenario,
    id: customScenarioId(scenario.id ?? 'scenario'),
    name: `${scenario.name || scenario.id || 'Сценарий'} (правка параметров)`,
    survey_type: options.survey_type,
    quality: { ...scenario.quality, gsd_m: parseRequired(options.gsd_m, 'GSD') },
    wind: {
      speed_mps: parseRequired(options.wind_speed_mps, 'Скорость ветра'),
      direction_deg: parseRequired(options.wind_direction_deg, 'Направление ветра'),
    },
    altitude_corridor: {
      ...scenario.altitude_corridor,
      max_altitude_agl_m: parseRequired(options.max_altitude_agl_m, 'Коридор высот'),
    },
    fleet: [first, ...rest],
    vehicles,
    start_time: options.start_time === '' ? null : `${options.start_time}:00Z`,
    deadline_s: deadlineHours === null ? null : deadlineHours * SECONDS_IN_HOUR,
    turnaround_s: turnaroundMinutes === null ? null : turnaroundMinutes * SECONDS_IN_MINUTE,
    min_sun_elevation_deg: sunDegrees,
  };
}
