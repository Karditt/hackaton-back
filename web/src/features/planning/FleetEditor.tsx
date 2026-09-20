import type { Site, Vehicle } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { MAX_FLEET_VEHICLES, fleetSize, newFleetRow, type FleetRow } from './scenarioOptions';
import styles from './PlanningForm.module.css';

type Props = {
  rows: FleetRow[];
  /** Каталог техники — `GET /vehicles`; борта вне каталога недопустимы. */
  vehicles: Vehicle[];
  /** Площадки выбранного сценария: приписка борта — только к своей площадке. */
  sites: Site[];
  onChange: (rows: FleetRow[]) => void;
  disabled: boolean;
};

/**
 * Состав флота: модель, число бортов, нагрузка, площадка, комплекты АКБ
 * (E10). Геометрия площадок не правится — выбирается из сценария (№8, №12).
 */
export function FleetEditor({ rows, vehicles, sites, onChange, disabled }: Props) {
  const total = fleetSize(rows);

  const update = (index: number, patch: Partial<FleetRow>) => {
    onChange(rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));
  };

  return (
    <fieldset className={styles.field}>
      <legend>
        Флот: {total} из {MAX_FLEET_VEHICLES}
      </legend>

      {rows.map((row, index) => {
        const vehicle = vehicles.find((item) => item.id === row.vehicle_id);
        return (
          <div className={styles.fleetRow} key={index}>
            <select
              className={styles.control}
              aria-label="Модель борта"
              value={row.vehicle_id}
              // Нагрузка принадлежит модели: при смене модели прежняя невалидна.
              onChange={(event) =>
                update(index, { vehicle_id: event.target.value, payload_id: '' })
              }
              disabled={disabled}
            >
              {vehicles.every((item) => item.id !== row.vehicle_id) && (
                <option value={row.vehicle_id}>{row.vehicle_id} — нет в каталоге</option>
              )}
              {vehicles.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>

            <div className={styles.fleetGrid}>
              <label className={styles.inline}>
                шт.
                <input
                  className={styles.number}
                  type="number"
                  min={1}
                  step={1}
                  value={row.count}
                  onChange={(event) => update(index, { count: event.target.value })}
                  disabled={disabled}
                />
              </label>

              <label className={styles.inline}>
                АКБ
                <input
                  className={styles.number}
                  type="number"
                  min={1}
                  step={1}
                  placeholder="∞"
                  title="Комплекты АКБ — предел числа вылетов борта; пусто — не ограничено"
                  value={row.battery_sets}
                  onChange={(event) => update(index, { battery_sets: event.target.value })}
                  disabled={disabled}
                />
              </label>

              <button
                type="button"
                className={styles.rowButton}
                onClick={() => onChange(rows.filter((_, position) => position !== index))}
                disabled={disabled || rows.length <= 1}
                title="Убрать борт из флота"
              >
                убрать
              </button>
            </div>

            <select
              className={styles.control}
              aria-label="Нагрузка"
              value={row.payload_id}
              onChange={(event) => update(index, { payload_id: event.target.value })}
              disabled={disabled}
            >
              <option value="">нагрузка — выберет модель съёмки</option>
              {(vehicle?.payloads ?? []).map((payload) => (
                <option key={payload.id} value={payload.id}>
                  {payload.name}
                </option>
              ))}
            </select>

            <select
              className={styles.control}
              aria-label="Площадка приписки"
              value={row.site_id}
              onChange={(event) => update(index, { site_id: event.target.value })}
              disabled={disabled}
            >
              <option value="">площадка — выберет распределение</option>
              {sites.map((site) => (
                <option key={site.id} value={site.id}>
                  {site.name || site.id}
                  {site.kind === 'reserve' ? ' (резервная)' : ''}
                </option>
              ))}
            </select>
          </div>
        );
      })}

      <button
        type="button"
        className={styles.rowButton}
        onClick={() => onChange([...rows, newFleetRow(vehicles[0]?.id ?? '')])}
        disabled={disabled || vehicles.length === 0}
      >
        + борт
      </button>

      {total > MAX_FLEET_VEHICLES && (
        <Notice tone="error">
          Во флоте {total} бортов, допустимо не больше {MAX_FLEET_VEHICLES} (заказчик, №2, №43).
        </Notice>
      )}
    </fieldset>
  );
}
