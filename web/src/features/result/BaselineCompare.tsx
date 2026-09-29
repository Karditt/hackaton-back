import { useState } from 'react';
import { getResult, startPlanning, waitForJob, type MissionMetrics } from '../../api';
import { hours } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import type { PlanningRun } from '../planning';
import styles from './result.module.css';

type Props = { run: PlanningRun };

type Comparison =
  | { state: 'idle' }
  | { state: 'running' }
  | { state: 'done'; baseline: MissionMetrics | null; reason?: string }
  | { state: 'error'; message: string };

/** Изменение плана против baseline, проценты со знаком: «−64%». */
function change(plan: number, baseline: number): string {
  if (baseline <= 0) return '—';
  const percent = Math.round((plan / baseline - 1) * 100);
  return `${percent > 0 ? '+' : percent < 0 ? '−' : ''}${Math.abs(percent)}%`;
}

/**
 * Сравнение с baseline (E12): тот же сценарий в том же режиме, но подобласти
 * делятся между бортами поровну по площади — так, как делил бы работу человек
 * без оптимизатора. Числа считает сервер; здесь только разница в процентах.
 */
export function BaselineCompare({ run }: Props) {
  const [comparison, setComparison] = useState<Comparison>({ state: 'idle' });
  const plan = run.result.mission.metrics;
  if (!plan) return null;

  const compare = async () => {
    setComparison({ state: 'running' });
    try {
      const job = await startPlanning(run.scenarioId, run.mode, 'baseline');
      const finished = await waitForJob(job.id, () => undefined);
      if (finished.state === 'failed') throw new Error(finished.error ?? 'расчёт не удался');
      const mission = (await getResult(job.id)).mission;
      setComparison({
        state: 'done',
        baseline: mission.metrics ?? null,
        reason: mission.reasons?.[0]?.message,
      });
    } catch (cause) {
      setComparison({ state: 'error', message: (cause as Error).message });
    }
  };

  if (comparison.state === 'idle' || comparison.state === 'running') {
    return (
      <p className={styles.scheduleLink}>
        Baseline — равное деление площади между бортами, без оптимизатора.{' '}
        <button
          type="button"
          className={styles.linkButton}
          disabled={comparison.state === 'running'}
          onClick={() => void compare()}
        >
          {comparison.state === 'running' ? 'Считаю baseline…' : 'Сравнить с baseline →'}
        </button>
      </p>
    );
  }
  if (comparison.state === 'error') return <Notice tone="error">{comparison.message}</Notice>;
  const baseline = comparison.baseline;
  if (!baseline) {
    return (
      <Notice tone="caution">
        Baseline с этим флотом задание не выполняет
        {comparison.reason ? `: ${comparison.reason}` : ''}. Оптимизатор выполняет.
      </Notice>
    );
  }

  const rows: [label: string, plan: number, baseline: number, text: (value: number) => string][] = [
    ['Срок', plan.makespan_s, baseline.makespan_s, hours],
    ['Ресурс', plan.resource_s, baseline.resource_s, hours],
    ['Налёт', plan.total_flight_time_s, baseline.total_flight_time_s, hours],
    ['Вылетов', plan.sortie_count, baseline.sortie_count, String],
  ];
  return (
    <table className={styles.compare}>
      <thead>
        <tr>
          <th />
          <th>План</th>
          <th>Baseline</th>
          <th>Разница</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, ours, theirs, text]) => (
          <tr key={label}>
            <td>{label}</td>
            <td>{text(ours)}</td>
            <td>{text(theirs)}</td>
            <td className={ours <= theirs ? styles.better : styles.worse}>
              {change(ours, theirs)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
