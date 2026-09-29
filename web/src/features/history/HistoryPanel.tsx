import { useCallback, useState } from 'react';
import { listJobs, type JobStatus } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import formStyles from '../planning/PlanningForm.module.css';
import styles from './history.module.css';

type Props = {
  /** Показать готовый план задачи как текущий расчёт. */
  onShow: (jobId: string, label: string) => void;
  /** Идёт расчёт или загрузка плана — открывать другой пока нельзя. */
  busy: boolean;
};

/** Момент задачи для человека: «29.09 14:05» по часам браузера. */
function when(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * История расчётов: готовые планы хранятся на сервере и переживают его
 * перезапуск. Любой можно открыть как текущий расчёт — карта, метрики,
 * расписание и выгрузка без пересчёта.
 */
export function HistoryPanel({ onShow, busy }: Props) {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<JobStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setJobs((await listJobs()).filter((job) => job.state === 'done'));
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  return (
    <details
      className={formStyles.params}
      open={open}
      onToggle={(event) => {
        const expanded = event.currentTarget.open;
        setOpen(expanded);
        if (expanded) void refresh();
      }}
    >
      <summary className={formStyles.summary}>История расчётов</summary>
      <p className={styles.lead}>
        Готовые планы хранятся на сервере — последние 30. Открыть можно любой: без пересчёта.{' '}
        <button type="button" className={styles.link} onClick={() => void refresh()}>
          Обновить
        </button>
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      {jobs !== null && jobs.length === 0 && <p className={styles.lead}>Расчётов пока нет.</p>}
      {jobs !== null && jobs.length > 0 && (
        <ul className={styles.list}>
          {jobs.map((job) => {
            const feasible = job.mission_status === 'feasible';
            const baseline = job.algorithm?.startsWith('baseline') ?? false;
            const name = baseline ? `${job.scenario_id}, baseline` : job.scenario_id;
            const label = `Расчёт «${name}» от ${when(job.finished_at)}`;
            return (
              <li key={job.id} className={styles.item}>
                <span className={styles.scenario} title={job.algorithm ?? undefined}>
                  {name}
                </span>
                <span className={styles.time}>{when(job.finished_at)}</span>
                <span className={feasible ? styles.good : styles.bad}>
                  {feasible ? 'выполним' : 'невыполним'}
                </span>
                <button
                  type="button"
                  className={styles.link}
                  disabled={busy}
                  onClick={() => onShow(job.id, label)}
                >
                  открыть
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </details>
  );
}
