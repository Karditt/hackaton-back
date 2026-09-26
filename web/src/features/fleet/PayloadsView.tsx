import { useEffect, useRef, useState } from 'react';
import type { Payload, PayloadKind, Vehicle } from '../../api';
import { decimal } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import {
  PAYLOAD_KINDS,
  PAYLOAD_SPEC_FIELDS,
  allPayloads,
  newPayload,
  payloadErrors,
  type CustomPayload,
  type PayloadSpecKey,
  type PayloadView,
} from './park';
import type { ParkState } from './usePark';
import styles from './fleet.module.css';

type Props = { vehicles: Vehicle[]; park: ParkState };

/** Число оптики для таблицы: у каталожной — из контракта, у своей — строка ввода. */
function specOf(payload: PayloadView, key: PayloadSpecKey): number | null {
  if (!payload.custom) return payload.payload[key] ?? null;
  const text = payload.payload.specs[key].trim().replace(',', '.');
  const value = Number(text);
  return text === '' || !Number.isFinite(value) ? null : value;
}

/** «36 × 24 мм», «8216 × 5477» — или прочерк, если одного из чисел нет. */
function pair(width: number | null, height: number | null, unit = ''): string {
  if (width === null || height === null) return '—';
  return `${decimal(width, 2)} × ${decimal(height, 2)}${unit}`;
}

/**
 * Полезная нагрузка: камеры, мультиспектральные камеры, тепловизоры, лидары —
 * по классам. Каталог показывается с источником и пробелами; своя нагрузка
 * описывается теми же четырьмя числами, что камера в GeoScan Planner:
 * фокусное, размер матрицы, размер кадра.
 */
export function PayloadsView({ vehicles, park }: Props) {
  const payloads = allPayloads(vehicles, park.park);
  const [selectedId, setSelectedId] = useState('');
  const [newKind, setNewKind] = useState<PayloadKind>('rgb');
  const selected = payloads.find((payload) => payload.id === selectedId) ?? null;

  const detail = useRef<HTMLElement | null>(null);
  useEffect(() => {
    detail.current?.scrollTo({ top: 0 });
  }, [selectedId]);

  const create = () => {
    const payload = newPayload(newKind);
    park.addPayload(payload);
    setSelectedId(payload.id);
  };

  return (
    <div className={`${styles.split} ${styles.splitTable}`}>
      <section className={styles.tablePane}>
        <div className={styles.toolbar}>
          <select
            className={styles.control}
            aria-label="Класс новой нагрузки"
            value={newKind}
            onChange={(event) => setNewKind(event.target.value as PayloadKind)}
          >
            {PAYLOAD_KINDS.map(([kind, label]) => (
              <option key={kind} value={kind}>
                {label}
              </option>
            ))}
          </select>
          <button type="button" className={styles.primary} onClick={create}>
            + Добавить нагрузку
          </button>
        </div>

        {PAYLOAD_KINDS.map(([kind, label]) => {
          const ofKind = payloads.filter((payload) => payload.kind === kind);
          if (ofKind.length === 0) return null;
          return (
            <div key={kind} className={styles.kindBlock}>
              <h3 className={styles.listHeading}>
                {label} · {ofKind.length}
              </h3>
              <table className={styles.dataTable}>
                <thead>
                  <tr>
                    <th>Нагрузка</th>
                    <th>Фокусное</th>
                    <th>Матрица</th>
                    <th>Кадр, пикс</th>
                    <th>На моделях</th>
                  </tr>
                </thead>
                <tbody>
                  {ofKind.map((payload) => {
                    const gaps = payload.custom ? [] : (payload.payload.gaps ?? []);
                    const focal = specOf(payload, 'focal_length_mm');
                    return (
                      <tr
                        key={payload.id}
                        className={payload.id === selected?.id ? styles.selectedRow : undefined}
                        onClick={() => setSelectedId(payload.id)}
                      >
                        <td>
                          <button type="button" className={styles.rowLink}>
                            {payload.name}
                          </button>
                          {payload.custom && <span className={styles.userBadge}>своя</span>}
                          {gaps.length > 0 && (
                            <span className={styles.gapBadge} title={gaps.join('\n')}>
                              пробел
                            </span>
                          )}
                        </td>
                        <td className={styles.num}>
                          {focal === null ? '—' : `${decimal(focal, 1)} мм`}
                        </td>
                        <td className={styles.num}>
                          {pair(
                            specOf(payload, 'sensor_width_mm'),
                            specOf(payload, 'sensor_height_mm'),
                            ' мм',
                          )}
                        </td>
                        <td className={styles.num}>
                          {pair(
                            specOf(payload, 'image_width_px'),
                            specOf(payload, 'image_height_px'),
                          )}
                        </td>
                        <td className={styles.muted}>
                          {payload.carriers.length > 0 ? payload.carriers.join(', ') : '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })}
      </section>

      <section className={styles.detailPane} ref={detail}>
        {selected === null && (
          <p className={styles.muted}>
            Выберите нагрузку в таблице — здесь будут источник чисел и пробелы, а свою нагрузку
            можно править.
          </p>
        )}
        {selected !== null && !selected.custom && <CatalogPayload payload={selected.payload} />}
        {selected !== null && selected.custom && (
          <CustomPayloadEditor
            key={selected.id}
            payload={selected.payload}
            park={park}
            onRemoved={() => setSelectedId('')}
          />
        )}
      </section>
    </div>
  );
}

/** Нагрузка каталога: источник, пробелы и выведенные поля. */
function CatalogPayload({ payload }: { payload: Payload }) {
  return (
    <div className={styles.editor}>
      <div className={styles.editorHead}>
        <h2 className={styles.detailTitle}>{payload.name}</h2>
        <span className={styles.catalogBadge}>каталог</span>
      </div>
      <h3 className={styles.subheading}>Источник</h3>
      <p className={styles.source}>{payload.source}</p>
      {(payload.derived_fields ?? []).length > 0 && (
        <p className={styles.hint}>
          Выведено по формуле из документированных чисел: {payload.derived_fields?.join(', ')}.
        </p>
      )}
      {(payload.gaps ?? []).length > 0 && (
        <>
          <h3 className={styles.subheading}>Пробелы</h3>
          <ul className={styles.plainList}>
            {payload.gaps?.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

type EditorProps = { payload: CustomPayload; park: ParkState; onRemoved: () => void };

/** Своя нагрузка: имя, класс, оптика, источник. */
function CustomPayloadEditor({ payload, park, onRemoved }: EditorProps) {
  const update = (patch: Partial<CustomPayload>) => park.updatePayload(payload.id, patch);
  const errors = payloadErrors(payload);
  return (
    <div className={styles.editor}>
      <div className={styles.editorHead}>
        <input
          className={styles.titleInput}
          aria-label="Название нагрузки"
          value={payload.name}
          onChange={(event) => update({ name: event.target.value })}
        />
        <span className={styles.userBadge}>своя</span>
      </div>
      <Notice tone="caution">
        Оптику своей нагрузки ввёл пользователь. В расчёт она уходит вместе с моделью, которая её
        несёт, — отметьте её в разделе «Модели и ТТХ».
      </Notice>

      <label className={styles.field}>
        Класс
        <select
          className={styles.control}
          value={payload.kind}
          onChange={(event) => update({ kind: event.target.value as PayloadKind })}
        >
          {PAYLOAD_KINDS.map(([kind, label]) => (
            <option key={kind} value={kind}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <table className={styles.specTable}>
        <tbody>
          {PAYLOAD_SPEC_FIELDS.map((field) => (
            <tr key={field.key}>
              <th scope="row">
                <label htmlFor={`${payload.id}-${field.key}`}>{field.label}</label>
              </th>
              <td>
                <span className={styles.inputUnit}>
                  <input
                    id={`${payload.id}-${field.key}`}
                    className={`${styles.control} ${errors[field.key] ? styles.invalid : ''}`}
                    inputMode="decimal"
                    value={payload.specs[field.key]}
                    placeholder="нет данных"
                    aria-invalid={errors[field.key] !== undefined}
                    onChange={(event) =>
                      update({ specs: { ...payload.specs, [field.key]: event.target.value } })
                    }
                  />
                  <span className={styles.unit}>{field.unit}</span>
                </span>
              </td>
              <td>
                {errors[field.key] && <span className={styles.error}>{errors[field.key]}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={styles.hint}>
        По фокусному, матрице и кадру ядро считает высоту съёмки для заданного GSD.
      </p>

      <label className={styles.field}>
        Источник чисел
        <input
          className={styles.control}
          value={payload.source}
          placeholder="паспорт камеры, страница документа"
          onChange={(event) => update({ source: event.target.value })}
        />
      </label>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.danger}
          onClick={() => {
            park.removePayload(payload.id);
            onRemoved();
          }}
        >
          Удалить нагрузку
        </button>
      </div>
    </div>
  );
}
