import { useCallback, useState } from 'react';
import {
  startPareto,
  waitForPareto,
  type ParetoStatus,
  type Scenario,
  type Vehicle,
} from '../../api';
import { scenarioToRun } from '../planning';
import type { ScenarioOptions } from '../planning/scenarioOptions';

export type ParetoParams = {
  scenarioId: string;
  /** Сохранённый сценарий; null — ещё не загружен. */
  scenario: Scenario | null;
  /** Параметры панели, возможно изменённые; null — ещё не загружены. */
  options: ScenarioOptions | null;
  /** Изменённые и свои модели флота с вкладки «Техника» — `Scenario.vehicles`. */
  vehicles: Vehicle[];
  /** Почему часть моделей флота нельзя отдать в расчёт; пусто — можно всё. */
  vehicleProblems: string[];
};

/**
 * Жизненный цикл фронта «срок — ресурс»: сохранение правок → постановка →
 * поллинг с готовыми точками → итог. Изменённые параметры и ТТХ уходят копией
 * сценария, как у расчёта плана (`scenarioToRun`).
 *
 * Фронт показывается, пока выбран тот же сценарий; правка параметров после
 * расчёта помечает его устаревшим — как подсказку по флоту.
 */
export function usePareto({
  scenarioId,
  scenario,
  options,
  vehicles,
  vehicleProblems,
}: ParetoParams) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<ParetoStatus | null>(null);
  const [baseScenarioId, setBaseScenarioId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [builtSignature, setBuiltSignature] = useState<string | null>(null);

  // Подпись выбора: сценарий, поля панели параметров и ТТХ. Режима здесь нет —
  // фронт сам проходит оба критерия.
  const signature = JSON.stringify([scenarioId, options, vehicles]);

  const build = useCallback(async () => {
    if (!scenarioId) return;
    if (vehicleProblems.length > 0) {
      setError(vehicleProblems.join('; '));
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    setBaseScenarioId(scenarioId);
    setStartedAt(Date.now());
    try {
      const frontScenarioId = await scenarioToRun(scenarioId, scenario, options, vehicles);
      const started = await startPareto(frontScenarioId);
      setStatus(started);
      const finished = await waitForPareto(started.id, setStatus);
      if (finished.state === 'failed') {
        throw new Error(finished.error ?? 'Фронт не построен');
      }
      setBuiltSignature(signature);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [scenarioId, scenario, options, vehicles, vehicleProblems, signature]);

  const visible = status !== null && baseScenarioId === scenarioId;
  return {
    build,
    busy,
    /** Последний опрос или итог; null — фронта нет или выбран другой сценарий. */
    status: visible ? status : null,
    /** Когда начат расчёт, миллисекунды эпохи — для времени ожидания. */
    startedAt,
    error,
    /** Параметры менялись после расчёта — фронт про прежние. */
    stale: visible && !busy && builtSignature !== signature,
  };
}

export type ParetoState = ReturnType<typeof usePareto>;
