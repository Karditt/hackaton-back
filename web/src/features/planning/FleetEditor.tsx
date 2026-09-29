import type { Site, Vehicle } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { MAX_FLEET_VEHICLES, fleetSize, newFleetRow, type FleetRow } from './scenarioOptions';
import styles from './PlanningForm.module.css';

/** Флот из парка техники: строки для бортов в строю и сколько бортов не взято. */
export type ParkFleet = { rows: FleetRow[]; skipped: number };

type Props = {
  rows: FleetRow[];
  /** Каталог техники — `GET /vehicles`; борта вне каталога недопустимы. */
  vehicles: Vehicle[];
  /** Площадки выбранного сценария: приписка борта — только к своей площадке. */
  sites: Site[];
  onChange: (rows: FleetRow[]) => void;
  disabled: boolean;
  /** Борта парка, которые в строю (вкладка «Техника»); null — парк не подключён. */
  parkFleet?: ParkFleet | null;
};

/**
 * Пул техники: модель, число бортов, нагрузка, площадка, комплекты АКБ
 * (E10). Какие борта пула пойдут в работу, решает расчёт — состав плана в
 * метриках. Геометрия площадок не правится — выбирается из сценария (№8, №12).
 */
export function FleetEditor({ rows, vehicles, sites, onChange, disabled, parkFleet }: Props) {
  const total = fleetSize(rows);
  const parkCount = parkFleet ? fleetSize(parkFleet.rows) : 0;

  const update = (index: number, patch: Partial<FleetRow>) => {
    onChange(rows.map((row, position) => (position === index ? { ...row, ...patch } : row)));
  };

  return (
    <fieldset className={styles.field}>
      <legend title="Доступная техника; какие борта пойдут в работу, решает расчёт">
        Пул техники: {total} из {MAX_FLEET_VEHICLES}
      </legend>

      {parkFleet && (
        <div className={styles.parkRow}>
          <button
            type="button"
            className={styles.rowButton}
            onClick={() => onChange(parkFleet.rows)}
            disabled={disabled || parkFleet.rows.length === 0}
            title={
              parkFleet.rows.length === 0
                ? 'В парке нет бортов в строю — вкладка «Техника»'
                : 'Заменить пул бортами парка, которые в строю; площадки подберёт расчёт'
            }
          >
            Взять борта в строю из парка ({parkCount})
          </button>
          {parkFleet.skipped > 0 && (
            <span className={styles.hint}>
              ещё {parkFleet.skipped} не взято: модель скрыта или в её ТТХ ошибки
            </span>
          )}
        </div>
      )}

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
                <option value={row.vehicle_id}>
                  {row.vehicle_id} — модель скрыта или её нет в каталоге
                </option>
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
                title="Убрать строку из пула"
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
              <option value="">нагрузка — подберёт расчёт</option>
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
              <option value="">площадка — подберёт расчёт</option>
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
          В пуле {total} бортов, а в один расчёт берётся не больше {MAX_FLEET_VEHICLES}.
        </Notice>
      )}
    </fieldset>
  );
}
