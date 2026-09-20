import { useCallback, useState } from 'react';
import {
  fetchMissionGeoJson,
  getResult,
  saveScenario,
  startPlanning,
  waitForJob,
  type GeoJsonDocument,
  type OptimizationMode,
  type PipelineResult,
  type Scenario,
} from '../../api';
import { applyOptions, isEdited, type ScenarioOptions } from './scenarioOptions';

export type PlanningParams = {
  scenarioId: string;
  /** Режим оптимизации: минимум срока или минимум ресурса (заказчик, №23). */
  mode: OptimizationMode;
  /** Сохранённый сценарий; null — ещё не загружен. */
  scenario: Scenario | null;
  /** Параметры панели, возможно правленные; null — ещё не загружены. */
  options: ScenarioOptions | null;
};

/** Завершённый расчёт. */
export type PlanningRun = {
  /** Что посчитано: выбранный сценарий или его правленная копия. */
  scenarioId: string;
  mode: OptimizationMode;
  jobId: string;
  result: PipelineResult;
  document: GeoJsonDocument;
};

/**
 * Жизненный цикл расчёта: сохранение правок → постановка → поллинг → результат.
 *
 * Правленные параметры уходят отдельным сценарием (`POST /scenarios`), и
 * считается он — исходный файл остаётся нетронутым. Проверку содержания
 * делает сервер: недопустимое значение возвращается как 422 с причиной и
 * показывается пользователем, а не чинится молча (заказчик, №19).
 *
 * Результат отдаётся наружу, только пока выбранные параметры совпадают с
 * теми, с которыми он посчитан. Иначе метрики прошлого расчёта легко принять
 * за текущие (долг №18 в docs/current_state.md).
 */
export function usePlanning({ scenarioId, mode, scenario, options }: PlanningParams) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('готов');
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<PlanningRun | null>(null);
  const [ranSignature, setRanSignature] = useState<string | null>(null);

  // Подпись выбора: сценарий, режим и все поля панели параметров.
  const signature = JSON.stringify([scenarioId, mode, options]);

  const start = useCallback(async () => {
    if (!scenarioId) return;
    setBusy(true);
    setError(null);
    setRun(null);
    setStatus('расчёт поставлен в очередь');
    try {
      let planId = scenarioId;
      if (scenario && options && isEdited(scenario, options)) {
        setStatus('сохраняю правленные параметры');
        planId = (await saveScenario(applyOptions(scenario, options))).id;
      }
      const job = await startPlanning(planId, mode);
      const finished = await waitForJob(job.id, (tick) => setStatus(`расчёт: ${tick.state}`));
      if (finished.state === 'failed') {
        throw new Error(finished.error ?? 'расчёт не удался');
      }
      const [result, document] = await Promise.all([
        getResult(job.id),
        fetchMissionGeoJson(job.id),
      ]);
      setRun({ scenarioId: planId, mode, jobId: job.id, result, document });
      setRanSignature(signature);
      setStatus(result.mission.status === 'feasible' ? 'план готов' : 'задание невыполнимо');
    } catch (cause) {
      setError((cause as Error).message);
      setStatus('ошибка');
    } finally {
      setBusy(false);
    }
  }, [scenarioId, mode, scenario, options, signature]);

  const isCurrent = run !== null && ranSignature === signature;
  const shownStatus = run !== null && !isCurrent ? 'параметры изменены — нужен пересчёт' : status;

  return {
    start,
    busy,
    status: shownStatus,
    error,
    run: isCurrent ? run : null,
  };
}
