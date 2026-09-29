import { useCallback, useState } from 'react';
import { listJobs, type JobStatus, type ScenarioSummary } from '../../api';
import { plural } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import formStyles from '../planning/PlanningForm.module.css';
import styles from './history.module.css';

type Props = {
  /** Сохранённые сценарии — название расчёта по идентификатору сценария. */
  scenarios: ScenarioSummary[];
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

/** Суффикс плана подсказки по флоту: `<сценарий>--fleet5`. */
const FLEET_SUFFIX = /--fleet(\d+)$/;

/**
 * История расчётов: готовые планы хранятся на сервере и переживают его
 * перезапуск. Любой можно открыть как текущий расчёт — карта, метрики,
 * расписание и выгрузка без пересчёта.
 */
export function HistoryPanel({ scenarios, onShow, busy }: Props) {
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

  /** Название расчёта: сценарий по имени, план подсказки — с числом бортов. */
  const title = (job: JobStatus): string => {
    const fleet = FLEET_SUFFIX.exec(job.scenario_id);
    const baseId = fleet ? job.scenario_id.slice(0, fleet.index) : job.scenario_id;
    const name = scenarios.find((item) => item.id === baseId)?.name || baseId;
    const parts = [name];
    if (fleet) {
      const count = Number(fleet[1]);
      parts.push(`подсказка, ${count} ${plural(count, ['борт', 'борта', 'бортов'])}`);
    }
    if (job.algorithm?.startsWith('baseline')) parts.push('равномерное деление');
    return parts.join(' · ');
  };

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
        Последние 30 готовых планов. Любой открывается сразу, без пересчёта.{' '}
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
            const name = title(job);
            const label = `Расчёт «${name}» от ${when(job.finished_at)}`;
            return (
              <li key={job.id} className={styles.item}>
                <span className={styles.scenario} title={name}>
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
