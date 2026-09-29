import { useState } from 'react';
import type { ParetoPoint } from '../../api';
import { hours, plural } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import formStyles from '../planning/PlanningForm.module.css';
import { ParetoChart } from './ParetoChart';
import type { ParetoState } from './usePareto';
import styles from './pareto.module.css';

/** Сколько планов строит фронт: два края и промежуточные сроки — как у сервера. */
const DEFAULT_POINTS = 6;

type Props = {
  front: ParetoState;
  /** Почему фронт не построить (сценарий только для просмотра); null — можно. */
  disabledNote: string | null;
  /** Сколько шёл последний расчёт этого сценария, секунды, — для оценки ожидания. */
  lastRunS: number | null;
  /** Показать план точки как текущий расчёт: карта, итог, расписание. */
  onShowPlan: (jobId: string, label: string) => void;
  /** Идёт расчёт или загрузка плана — план точки пока не показать. */
  planBusy: boolean;
};

/** @param seconds длительность, секунды: короткая — в секундах, длинная — в часах и минутах */
function waitText(seconds: number): string {
  return seconds < 90 ? `${Math.round(seconds)} с` : hours(seconds);
}

/** Одинаковые планы: срок и ресурс совпадают до секунды. */
function samePlan(a: ParetoPoint, b: ParetoPoint): boolean {
  return (
    a.makespan_s !== null &&
    b.makespan_s !== null &&
    Math.abs(a.makespan_s - b.makespan_s) < 1 &&
    Math.abs((a.resource_s ?? 0) - (b.resource_s ?? 0)) < 1
  );
}

/**
 * Строки таблицы: точка и сколько следующих за ней сроков дали тот же план.
 * Короткий срок часто упирается в тот же план, что «минимум срока», — четыре
 * одинаковые строки подряд ничего не говорят.
 */
function uniqueRows(points: ParetoPoint[]): { index: number; repeats: number }[] {
  const rows: { index: number; repeats: number }[] = [];
  points.forEach((point, index) => {
    const last = rows[rows.length - 1];
    if (last && samePlan(points[last.index], point)) last.repeats += 1;
    else rows.push({ index, repeats: 0 });
  });
  return rows;
}

/** Точка по умолчанию: самая экономная среди недоминируемых выполнимых. */
function defaultPoint(points: ParetoPoint[]): number | null {
  let best: number | null = null;
  points.forEach((point, index) => {
    if (point.resource_s === null || point.dominated) return;
    if (best === null || point.resource_s < (points[best].resource_s ?? Infinity)) best = index;
  });
  return best;
}

/**
 * Фронт «срок — ресурс»: что стоит закончить работы раньше.
 *
 * Края — планы «минимум срока» и «минимум ресурса», между ними — самый
 * экономный план к промежуточным срокам. Каждая точка — полный план с
 * проверкой безопасности, его можно открыть как обычный расчёт.
 */
export function ParetoPanel({ front, disabledNote, lastRunS, onShowPlan, planBusy }: Props) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<number | null>(null);
  const status = front.status;
  const points = status?.points ?? [];
  const done = status?.state === 'done';
  const selected =
    picked !== null && picked < points.length ? picked : done ? defaultPoint(points) : null;
  const point = selected !== null ? points[selected] : null;
  const expected = status?.expected_points ?? DEFAULT_POINTS;
  const elapsedS = front.startedAt === null ? 0 : (Date.now() - front.startedAt) / 1000;

  const fastest = points.find((item) => item.mode === 'min_makespan' && item.makespan_s !== null);
  const frugal = [...points]
    .reverse()
    .find(
      (item) =>
        item.mode === 'min_resource' && item.deadline_s === null && item.resource_s !== null,
    );

  return (
    <details
      className={formStyles.params}
      open={open || front.busy}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className={formStyles.summary}>
        Срок или ресурс
        {front.busy && <span className={styles.badgeRunning}>идёт</span>}
        {!front.busy && done && (
          <span className={front.stale ? formStyles.badge : styles.badgeReady}>
            {front.stale ? 'устарел' : 'готов'}
          </span>
        )}
      </summary>
      <p className={styles.lead}>
        Сколько ресурса техники стоит закончить работы раньше. Края — планы «минимум срока» и
        «минимум ресурса», между ними — самый экономный план к промежуточным срокам. Каждая точка —
        полный план с проверкой безопасности.
      </p>
      <p className={styles.note}>
        Будет {expected} расчётов плана.{' '}
        {lastRunS !== null
          ? `Расчёт этого сценария шёл ${waitText(lastRunS)}: ждать около ${waitText(expected * lastRunS)}.`
          : 'На больших областях каждый расчёт идёт минуты, на небольших — секунды.'}
      </p>

      <button
        type="button"
        className={styles.build}
        disabled={front.busy || disabledNote !== null}
        title={disabledNote ?? undefined}
        onClick={() => {
          setPicked(null);
          void front.build();
        }}
      >
        {front.busy ? 'Считаю…' : done ? 'Построить заново' : 'Построить фронт'}
      </button>
      {disabledNote && <Notice tone="caution">{disabledNote}</Notice>}

      {front.busy && (
        <div className={styles.progressBox} role="status">
          <progress className={styles.progress} value={status?.progress ?? 0} max={1} />
          <span>
            Готово планов: {points.length} из {expected} · {waitText(elapsedS)}
          </span>
        </div>
      )}
      {front.error && <Notice tone="error">{front.error}</Notice>}

      {points.length > 0 && (
        <div className={styles.result}>
          {front.stale && (
            <Notice tone="caution">
              Параметры изменены после расчёта — фронт про прежние. Постройте заново.
            </Notice>
          )}
          {done &&
            fastest &&
            frugal &&
            fastest.makespan_s !== null &&
            frugal.makespan_s !== null && (
              <p className={styles.summaryText}>
                Быстрее всего — {hours(fastest.makespan_s)} за {hours(fastest.resource_s ?? 0)}{' '}
                ресурса; экономнее всего — {hours(frugal.resource_s ?? 0)} ресурса за{' '}
                {hours(frugal.makespan_s)}.
              </p>
            )}
          <ParetoChart points={points} selected={selected} onSelect={setPicked} />
          <p className={styles.legend}>
            <span className={styles.legendFront} /> выбор «быстрее или дешевле»{' '}
            <span className={styles.legendDominated} /> план не лучше соседнего ни по сроку, ни по
            ресурсу
          </p>

          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>План</th>
                  <th>Срок</th>
                  <th>Ресурс</th>
                  <th title="Вылетов / бортов">Выл./борт.</th>
                </tr>
              </thead>
              <tbody>
                {uniqueRows(points).map(({ index, repeats }) => {
                  const item = points[index];
                  return (
                    <tr
                      key={`${item.mode}-${item.deadline_s ?? 'free'}`}
                      className={index === selected ? styles.selectedRow : undefined}
                      onClick={() => setPicked(index)}
                    >
                      <td className={item.dominated ? styles.muted : undefined}>
                        {item.label}
                        {repeats > 0 && (
                          <span className={styles.repeats}>
                            {' '}
                            и ещё {repeats} {plural(repeats, ['срок', 'срока', 'сроков'])} — тот же
                            план
                          </span>
                        )}
                      </td>
                      <td>{item.makespan_s === null ? '—' : hours(item.makespan_s)}</td>
                      <td>{item.resource_s === null ? '—' : hours(item.resource_s)}</td>
                      <td>
                        {item.sortie_count === null
                          ? 'невыполним'
                          : `${item.sortie_count} / ${item.vehicle_count}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {point && point.plan_job_id && (
            <button
              type="button"
              className={styles.showPlan}
              disabled={front.stale || planBusy}
              title={front.stale ? 'Фронт устарел — постройте заново' : undefined}
              onClick={() =>
                point.plan_job_id && onShowPlan(point.plan_job_id, `Фронт: ${point.label}`)
              }
            >
              Показать план «{point.label}» на карте
            </button>
          )}
        </div>
      )}
    </details>
  );
}
