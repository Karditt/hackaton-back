import { useCallback, useState } from 'react';
import {
  fetchMissionGeoJson,
  getResult,
  startPlanning,
  waitForJob,
  type GeoJsonDocument,
  type OptimizationMode,
  type PipelineResult,
} from '../../api';

export type PlanningParams = {
  scenarioId: string;
  /** Режим оптимизации: минимум срока или минимум ресурса (заказчик, №23). */
  mode: OptimizationMode;
};

/** Завершённый расчёт вместе с параметрами, с которыми он получен. */
export type PlanningRun = PlanningParams & {
  jobId: string;
  result: PipelineResult;
  document: GeoJsonDocument;
};

/**
 * Жизненный цикл расчёта: постановка → поллинг → результат.
 *
 * Результат отдаётся наружу, только пока выбранные параметры совпадают с теми,
 * с которыми он посчитан. Иначе метрики прошлого сценария легко принять за
 * текущие (долг №18 в docs/current_state.md).
 */
export function usePlanning({ scenarioId, mode }: PlanningParams) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('готов');
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<PlanningRun | null>(null);

  const start = useCallback(async () => {
    if (!scenarioId) return;
    setBusy(true);
    setError(null);
    setRun(null);
    setStatus('расчёт поставлен в очередь');
    try {
      const job = await startPlanning(scenarioId, mode);
      const finished = await waitForJob(job.id, (tick) => setStatus(`расчёт: ${tick.state}`));
      if (finished.state === 'failed') {
        throw new Error(finished.error ?? 'расчёт не удался');
      }
      const [result, document] = await Promise.all([
        getResult(job.id),
        fetchMissionGeoJson(job.id),
      ]);
      setRun({ scenarioId, mode, jobId: job.id, result, document });
      setStatus(result.mission.status === 'feasible' ? 'план готов' : 'задание невыполнимо');
    } catch (cause) {
      setError((cause as Error).message);
      setStatus('ошибка');
    } finally {
      setBusy(false);
    }
  }, [scenarioId, mode]);

  const isCurrent = run !== null && run.scenarioId === scenarioId && run.mode === mode;
  const shownStatus = run !== null && !isCurrent ? 'параметры изменены — нужен пересчёт' : status;

  return {
    start,
    busy,
    status: shownStatus,
    error,
    run: isCurrent ? run : null,
  };
}
