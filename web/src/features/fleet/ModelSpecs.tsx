import type { Vehicle } from '../../api';
import { decimal, hours } from '../../shared/format';
import { gapBody } from './gaps';
import {
  VEHICLE_TYPES,
  catalogSpecs,
  specErrors,
  specFieldsFor,
  type ModelView,
  type SpecField,
  type SpecKey,
} from './park';
import styles from './fleet.module.css';

/** Метров в секунду в километрах в час — только перевод единиц показа. */
const KMH_PER_MPS = 3.6;

/** Режимы взлёта и посадки каталога — подписи. */
const MODE_LABELS: Record<string, string> = {
  catapult: 'катапульта',
  parachute: 'парашют',
  vertical: 'вертикально',
};

/** Откуда число ТТХ модели каталога. */
type Origin = { tone: 'document' | 'derived' | 'gap' | 'none'; note: string };

const ORIGIN_LABELS: Record<Origin['tone'], string> = {
  document: 'документ',
  derived: 'расчётное',
  gap: 'нет в документах',
  none: 'не задано',
};

/**
 * Происхождение числа каталога: пробел — если его нет и каталог объясняет
 * почему; расчётное — если поле в `derived_fields`; иначе — из документа.
 */
function originOf(vehicle: Vehicle, key: SpecKey): Origin {
  const gap = (vehicle.gaps ?? []).find((text) => text.startsWith(`${key}:`));
  const note = gap ? gapBody(gap) : '';
  const value = vehicle[key];
  if (value === null || value === undefined) {
    return gap
      ? { tone: 'gap', note }
      : { tone: 'none', note: 'Производитель не публикует — модель этим не ограничивается' };
  }
  if ((vehicle.derived_fields ?? []).includes(key)) {
    return {
      tone: 'derived',
      note: note || 'В документах производителя нет — рассчитано из других характеристик',
    };
  }
  return { tone: 'document', note: 'Из документации производителя' };
}

/** Число ТТХ в единицах показа: скорость — ещё и в км/ч, время — ещё и в часах. */
export function formatSpec(value: number, field: SpecField): string {
  const shown = value / field.scale;
  let text = `${decimal(shown, shown >= 100 ? 0 : 1)} ${field.unit}`;
  if (field.unit === 'м/с') text += ` (${decimal(value * KMH_PER_MPS, 0)} км/ч)`;
  if (field.key === 'endurance_s' && shown >= 60) text += ` (${hours(value)})`;
  return text;
}

function typeLabel(type: string): string {
  return VEHICLE_TYPES.find(([id]) => id === type)?.[1] ?? type;
}

/** Одна строка о модели: тип, крейсерская скорость, продолжительность, ветер. */
export function ModelSummary({ model }: { model: ModelView }) {
  const fields = specFieldsFor(model.type).filter((field) =>
    ['cruise_speed_mps', 'endurance_s', 'max_wind_mps'].includes(field.key),
  );
  const parts = fields.map((field) => {
    const text = model.specs[field.key].trim();
    return `${field.label.toLowerCase()} ${text ? `${text} ${field.unit}` : '—'}`;
  });
  return (
    <p className={styles.summary}>
      <b>{typeLabel(model.type)}</b> · {parts.join(' · ')}
    </p>
  );
}

type Props = {
  model: ModelView;
  /** Новое значение поля в единицах показа. */
  onChange: (key: SpecKey, value: string) => void;
  /** Вернуть поле модели каталога к числу каталога. */
  onReset?: (key: SpecKey) => void;
  /** Отсечка заряда, доля 0..1 — общая норма, одна на все борта. */
  reserveFraction?: number;
};

/**
 * ТТХ модели таблицей, с правкой.
 *
 * У модели каталога рядом с каждым числом — откуда оно: из документа,
 * выведено правилом, пробел. Правленое поле помечено «изменено», и его можно
 * вернуть к числу каталога. У своей модели все числа — ввод пользователя.
 * Проверяется только форма чисел; противоречия между ними называет ядро на
 * приёме сценария.
 */
export function ModelSpecs({ model, onChange, onReset, reserveFraction }: Props) {
  const errors = specErrors(model);
  const original = model.base ? catalogSpecs(model.base) : null;

  return (
    <table className={styles.specTable}>
      <tbody>
        {specFieldsFor(model.type).map((field) => {
          const error = errors[field.key];
          const edited = model.edited.includes(field.key);
          const id = `${model.id}-${field.key}`;
          let status;
          if (error) {
            status = <span className={styles.error}>{error}</span>;
          } else if (model.base === null) {
            status = <span className={styles.origin_user}>ваше значение</span>;
          } else if (edited) {
            const before = model.base[field.key];
            status = (
              <span className={styles.statusCell}>
                <span className={styles.origin_user}>изменено</span>
                <button
                  type="button"
                  className={styles.revert}
                  onClick={() => onReset?.(field.key)}
                  title={`Вернуть из каталога: ${
                    before === null || before === undefined
                      ? 'нет данных'
                      : formatSpec(before, field)
                  }`}
                  aria-label={`Вернуть «${field.label}» из каталога`}
                >
                  ↺
                </button>
              </span>
            );
          } else {
            const origin = originOf(model.base, field.key);
            status = (
              <span className={styles[`origin_${origin.tone}`]} title={origin.note}>
                {ORIGIN_LABELS[origin.tone]}
              </span>
            );
          }
          return (
            <tr key={field.key} className={edited ? styles.editedRow : undefined}>
              <th scope="row">
                <label htmlFor={id}>
                  {field.label}
                  {field.required && <span className={styles.required}> *</span>}
                </label>
              </th>
              <td>
                <span className={styles.inputUnit}>
                  <input
                    id={id}
                    className={`${styles.control} ${error ? styles.invalid : ''}`}
                    inputMode="decimal"
                    value={model.specs[field.key]}
                    placeholder={original?.[field.key] === '' ? 'нет данных' : 'пусто'}
                    aria-invalid={error !== undefined}
                    onChange={(event) => onChange(field.key, event.target.value)}
                  />
                  <span className={styles.unit}>{field.unit}</span>
                </span>
              </td>
              <td>{status}</td>
            </tr>
          );
        })}
        {reserveFraction !== undefined && (
          <tr>
            <th scope="row">Отсечка заряда</th>
            <td className={styles.specValue}>{decimal(reserveFraction * 100, 0)} %</td>
            <td>
              <span
                className={styles.origin_customer}
                title="Посадка с остатком заряда не меньше этой доли — одна норма на все борта, у модели не правится"
              >
                норма
              </span>
            </td>
          </tr>
        )}
        {model.base && (model.base.takeoff_mode || model.base.landing_mode) && (
          <tr>
            <th scope="row">Взлёт / посадка</th>
            <td className={styles.specValue}>
              {MODE_LABELS[model.base.takeoff_mode ?? ''] ?? model.base.takeoff_mode ?? '—'} /{' '}
              {MODE_LABELS[model.base.landing_mode ?? ''] ?? model.base.landing_mode ?? '—'}
            </td>
            <td>
              <span className={styles.origin_document}>документ</span>
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
