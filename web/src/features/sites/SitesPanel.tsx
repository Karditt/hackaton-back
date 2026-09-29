import { useState } from 'react';
import type { SiteTrial } from '../../api';
import { decimal, hours } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import formStyles from '../planning/PlanningForm.module.css';
import styles from '../pareto/pareto.module.css';
import type { SiteSearchState } from './useSiteSearch';

/** Сколько расчётов в подборе: «как есть» и три доли дальней работы — как у сервера. */
const DEFAULT_TRIALS = 4;

type Props = {
  search: SiteSearchState;
  /** Почему подбор не запустить (сценарий только для просмотра); null — можно. */
  disabledNote: string | null;
  /** Сколько шёл последний расчёт этого сценария, секунды, — для оценки ожидания. */
  lastRunS: number | null;
  /** Показать план пробы как текущий расчёт: карта с предложенной площадкой. */
  onShowPlan: (jobId: string, label: string) => void;
  /** Идёт расчёт или загрузка плана — план пробы пока не показать. */
  planBusy: boolean;
};

/** @param seconds длительность, секунды: короткая — в секундах, длинная — в часах и минутах */
function waitText(seconds: number): string {
  return seconds < 90 ? `${Math.round(seconds)} с` : hours(seconds);
}

/** Изменение срока против «как есть», проценты со знаком: «−6%». */
function change(value: number, base: number): string {
  const percent = Math.round((value / base - 1) * 100);
  return `${percent > 0 ? '+' : percent < 0 ? '−' : ''}${Math.abs(percent)}%`;
}

/** Место площадки для человека: «59,7123° с. ш., 30,1234° в. д.». */
function place(trial: SiteTrial): string {
  if (!trial.position) return '';
  const { lat, lon } = trial.position;
  return `${decimal(Math.abs(lat), 4)}° ${lat >= 0 ? 'с. ш.' : 'ю. ш.'}, ${decimal(Math.abs(lon), 4)}° ${lon >= 0 ? 'в. д.' : 'з. д.'}`;
}

/** Лучший кандидат: самый короткий срок среди выполнимых кандидатов. */
function bestCandidate(trials: SiteTrial[]): number | null {
  let best: number | null = null;
  trials.forEach((trial, index) => {
    if (trial.position === null || trial.makespan_s === null) return;
    if (best === null || trial.makespan_s < (trials[best].makespan_s ?? Infinity)) best = index;
  });
  return best;
}

/**
 * Где поставить ещё площадку: кандидаты у самой дальней от площадок работы,
 * каждый проверен полным расчётом против расчёта «как есть».
 */
export function SitesPanel({ search, disabledNote, lastRunS, onShowPlan, planBusy }: Props) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<number | null>(null);
  const status = search.status;
  const trials = status?.trials ?? [];
  const done = status?.state === 'done';
  const base = trials[0]?.position === null ? trials[0] : null;
  const best = bestCandidate(trials);
  const selected = picked !== null && picked < trials.length ? picked : done ? best : null;
  const trial = selected !== null ? trials[selected] : null;
  const expected = status?.expected_trials ?? DEFAULT_TRIALS;
  const elapsedS = search.startedAt === null ? 0 : (Date.now() - search.startedAt) / 1000;

  let answer: string | null = null;
  if (done && base?.makespan_s != null && best !== null) {
    const winner = trials[best];
    const gain = (winner.makespan_s as number) < base.makespan_s - 60;
    answer = gain
      ? `Лучшее место — ${winner.label}: срок ${hours(winner.makespan_s as number)} вместо ${hours(base.makespan_s)} (${change(winner.makespan_s as number, base.makespan_s)}).`
      : `Новая площадка срок не сокращает: «как есть» — ${hours(base.makespan_s)}, с лучшим кандидатом — ${hours(winner.makespan_s as number)}.`;
  } else if (done && base && !base.feasible && best !== null) {
    answer = `Без новой площадки задание невыполнимо; ${trials[best].label} — срок ${hours(trials[best].makespan_s as number)}.`;
  } else if (done && best === null) {
    answer = 'Ни с одним кандидатом задание не выполнить.';
  }

  return (
    <details
      className={formStyles.params}
      open={open || search.busy}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className={formStyles.summary}>
        Где поставить ещё площадку
        {search.busy && <span className={styles.badgeRunning}>идёт</span>}
        {!search.busy && done && (
          <span className={search.stale ? formStyles.badge : styles.badgeReady}>
            {search.stale ? 'устарел' : 'готов'}
          </span>
        )}
      </summary>
      <p className={styles.lead}>
        Сервис предлагает место ещё одной площадки — у самой дальней от площадок работы — и
        проверяет каждое полным расчётом с проверкой безопасности. Для сравнения — расчёт «как
        есть»; площадки бортам в обоих выбирает расчёт.
      </p>
      <p className={styles.note}>
        Будет до {expected} расчётов плана.{' '}
        {lastRunS !== null
          ? `Расчёт этого сценария шёл ${waitText(lastRunS)}: ждать около ${waitText(expected * lastRunS)}.`
          : 'На больших областях каждый расчёт идёт минуты, на небольших — секунды.'}
      </p>

      <button
        type="button"
        className={styles.build}
        disabled={search.busy || disabledNote !== null}
        title={disabledNote ?? undefined}
        onClick={() => {
          setPicked(null);
          void search.search();
        }}
      >
        {search.busy ? 'Считаю…' : done ? 'Подобрать заново' : 'Подобрать место'}
      </button>
      {disabledNote && <Notice tone="caution">{disabledNote}</Notice>}

      {search.busy && (
        <div className={styles.progressBox} role="status">
          <progress className={styles.progress} value={status?.progress ?? 0} max={1} />
          <span>
            Готово расчётов: {trials.length} из {expected} · {waitText(elapsedS)}
          </span>
        </div>
      )}
      {search.error && <Notice tone="error">{search.error}</Notice>}

      {trials.length > 0 && (
        <div className={styles.result}>
          {search.stale && (
            <Notice tone="caution">
              Параметры изменены после подбора — он про прежние. Подберите заново.
            </Notice>
          )}
          {answer && <p className={styles.summaryText}>{answer}</p>}

          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Вариант</th>
                  <th>Срок</th>
                  <th title="Изменение срока против «как есть»">Δ</th>
                  <th>Ресурс</th>
                </tr>
              </thead>
              <tbody>
                {trials.map((item, index) => (
                  <tr
                    key={`${item.label}-${index}`}
                    className={index === selected ? styles.selectedRow : undefined}
                    onClick={() => setPicked(index)}
                  >
                    <td>{item.label}</td>
                    <td>{item.makespan_s === null ? 'невыполним' : hours(item.makespan_s)}</td>
                    <td>
                      {item.position !== null && item.makespan_s !== null && base?.makespan_s
                        ? change(item.makespan_s, base.makespan_s)
                        : '—'}
                    </td>
                    <td>{item.resource_s === null ? '—' : hours(item.resource_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {trial && trial.position && <p className={styles.note}>Место: {place(trial)}</p>}
          {trial && (
            <button
              type="button"
              className={styles.showPlan}
              disabled={search.stale || planBusy}
              title={search.stale ? 'Подбор устарел — подберите заново' : undefined}
              onClick={() =>
                onShowPlan(
                  trial.plan_job_id,
                  trial.position ? `Площадка: ${trial.label}` : 'Площадки: как есть',
                )
              }
            >
              {trial.position
                ? 'Показать план с этой площадкой на карте'
                : 'Показать план «как есть»'}
            </button>
          )}
        </div>
      )}
    </details>
  );
}
