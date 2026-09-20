import { Fragment } from 'react';
import type { BoardSummary, Schedule, ScheduleEntry } from '../../api';
import { clockTime, hours, minutes } from '../../shared/format';
import styles from './result.module.css';

type Props = {
  schedule: Schedule;
  /** Сводка бортов выгрузки — даёт подписи «Геоскан Gemini #3» и порядок строк. */
  boards: BoardSummary[];
};

/** Цвета моделей: борта одной модели — одним цветом, порядок — по первой встрече. */
const MODEL_COLORS = ['#2563eb', '#16a34a', '#b45309', '#7c3aed', '#0891b2', '#db2777'];

/** Сколько делений подписывается на шкале времени. */
const TICKS = 4;

/** Ключ борта: модель и номер экземпляра — так же, как их различает контракт. */
function boardKey(item: { vehicle_id: string; vehicle_instance: number }): string {
  return `${item.vehicle_id}/${item.vehicle_instance}`;
}

/**
 * Диаграмма расписания по бортам (заказчик, №31).
 *
 * Полоса — вылет от взлёта до посадки; штриховка перед ней — ожидание на
 * земле, на которое расписание сдвинуло старт ради разведения бортов
 * (`delay_s`). Ничего не считается: все моменты приходят с сервера.
 *
 * Диаграмма показывает то, что таблицей не видно, — перекос загрузки между
 * бортами. У baseline он велик: в демо один коптер делает 21 вылет, другой 7
 * (долг №25, задача E6 шаг 2).
 */
export function ScheduleGantt({ schedule, boards }: Props) {
  const entries = schedule.entries ?? [];
  if (entries.length === 0) {
    return <p className={styles.empty}>Расписание пусто.</p>;
  }

  const span = Math.max(...entries.map((entry) => entry.end_s));
  const origin = schedule.start_time ? Date.parse(schedule.start_time) : null;

  const rows = new Map<string, ScheduleEntry[]>();
  for (const board of boards) {
    rows.set(boardKey(board), []);
  }
  for (const entry of entries) {
    const key = boardKey(entry);
    const row = rows.get(key);
    if (row) row.push(entry);
    else rows.set(key, [entry]);
  }

  const labels = new Map(boards.map((board) => [boardKey(board), board.label]));
  const models = [...new Set([...rows.keys()].map((key) => key.split('/')[0]))];

  const moment = (seconds: number) =>
    origin === null ? `T+${minutes(seconds)}` : clockTime(origin, seconds);

  return (
    <div className={styles.gantt}>
      {[...rows].map(([key, own]) => {
        const [vehicleId, instance] = key.split('/');
        const color = MODEL_COLORS[models.indexOf(vehicleId) % MODEL_COLORS.length];
        return (
          <div className={styles.ganttRow} key={key}>
            <span className={styles.ganttLabel} title={`${vehicleId} #${Number(instance) + 1}`}>
              {labels.get(key) ?? `${vehicleId} #${Number(instance) + 1}`}
              <span className={styles.ganttCount}>{own.length}</span>
            </span>
            <span className={styles.ganttTrack}>
              {own.map((entry) => (
                <Fragment key={entry.sortie_id}>
                  {entry.delay_s > 0 && (
                    <span
                      className={styles.ganttWait}
                      style={{
                        left: `${((entry.start_s - entry.delay_s) / span) * 100}%`,
                        width: `${(entry.delay_s / span) * 100}%`,
                      }}
                      title={`${entry.sortie_id}: ожидание на земле ${hours(entry.delay_s)}`}
                    />
                  )}
                  <span
                    className={styles.ganttBar}
                    style={{
                      left: `${(entry.start_s / span) * 100}%`,
                      width: `${Math.max(((entry.end_s - entry.start_s) / span) * 100, 0.4)}%`,
                      background: color,
                    }}
                    title={
                      `${entry.sortie_id}\n` +
                      `взлёт ${moment(entry.start_s)} · посадка ${moment(entry.end_s)}\n` +
                      `в воздухе ${hours(entry.end_s - entry.start_s)}` +
                      (entry.delay_s > 0 ? ` · ожидание ${hours(entry.delay_s)}` : '') +
                      `\nэшелон ${Math.round(entry.transit_altitude_m)} м · площадки ` +
                      `${entry.takeoff_site_id} → ${entry.landing_site_id}`
                    }
                  />
                </Fragment>
              ))}
            </span>
          </div>
        );
      })}

      <div className={styles.ganttAxis}>
        {Array.from({ length: TICKS + 1 }, (_, index) => (
          <span key={index}>{hours((span * index) / TICKS)}</span>
        ))}
      </div>
      <p className={styles.hint}>
        Полоса — вылет, штриховка перед ней — ожидание на земле ради разведения бортов. Число
        у борта — сколько у него вылетов.
      </p>
    </div>
  );
}
