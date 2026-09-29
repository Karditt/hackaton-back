import type { BoardSummary, Schedule } from '../../api';
import { clockTime, hours, minutes } from '../../shared/format';
import styles from './result.module.css';

type Props = {
  schedule: Schedule;
  /** Сводка бортов выгрузки — подпись борта как на диаграмме. */
  boards?: BoardSummary[];
  /** Названия площадок по идентификатору; нет в словаре — идентификатор. */
  sites?: Map<string, string>;
  /**
   * Подробная таблица для вкладки «Расписание»: время в воздухе, ожидание на
   * земле, эшелон перелёта, площадка посадки; вся высота, без прокрутки внутри.
   */
  detailed?: boolean;
};

/**
 * Расписание вылетов (заказчик, №31). Время — от начала работ; если начало
 * задано в сценарии, рядом — абсолютное время UTC.
 */
export function ScheduleTable({ schedule, boards = [], sites, detailed = false }: Props) {
  const entries = schedule.entries ?? [];
  const origin = schedule.start_time ? Date.parse(schedule.start_time) : null;
  const labels = new Map(
    boards.map((board) => [`${board.vehicle_id}/${board.vehicle_instance}`, board.label]),
  );
  const at = (seconds: number) => (origin === null ? minutes(seconds) : clockTime(origin, seconds));
  const site = (siteId: string) => sites?.get(siteId) ?? siteId;

  return (
    <div className={detailed ? styles.tableWrapFull : styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Борт</th>
            <th>Вылет</th>
            <th>Взлёт{origin !== null && ', UTC'}</th>
            <th>Посадка{origin !== null && ', UTC'}</th>
            {detailed && <th>В воздухе</th>}
            {detailed && <th>Ожидание на земле</th>}
            {detailed && <th>Эшелон, м</th>}
            <th>Площадка</th>
            {detailed && <th>Посадка на</th>}
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.sortie_id}>
              <td>
                {labels.get(`${entry.vehicle_id}/${entry.vehicle_instance}`) ??
                  `${entry.vehicle_id} #${entry.vehicle_instance + 1}`}
              </td>
              <td>{entry.trip_index + 1}</td>
              <td>{at(entry.start_s)}</td>
              <td>{at(entry.end_s)}</td>
              {detailed && <td>{hours(entry.end_s - entry.start_s)}</td>}
              {detailed && <td>{entry.delay_s > 0 ? hours(entry.delay_s) : '—'}</td>}
              {detailed && <td>{Math.round(entry.transit_altitude_m)}</td>}
              <td>{site(entry.takeoff_site_id)}</td>
              {detailed && <td>{site(entry.landing_site_id)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
