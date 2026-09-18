import type { Schedule } from '../../api';
import { clockTime, minutes } from '../../shared/format';
import styles from './result.module.css';

type Props = { schedule: Schedule };

/**
 * Расписание вылетов (заказчик, №31). Время — от начала работ; если начало
 * задано в сценарии, рядом — абсолютное время UTC.
 */
export function ScheduleTable({ schedule }: Props) {
  const entries = schedule.entries ?? [];
  const origin = schedule.start_time ? Date.parse(schedule.start_time) : null;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Борт</th>
            <th>Вылет</th>
            <th>Взлёт</th>
            <th>Посадка</th>
            <th>Площадка</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.sortie_id}>
              <td>
                {entry.vehicle_id} #{entry.vehicle_instance + 1}
              </td>
              <td>{entry.trip_index + 1}</td>
              <td>{origin === null ? minutes(entry.start_s) : clockTime(origin, entry.start_s)}</td>
              <td>{origin === null ? minutes(entry.end_s) : clockTime(origin, entry.end_s)}</td>
              <td>{entry.takeoff_site_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
