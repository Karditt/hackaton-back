import { useCallback, useState } from 'react';
import { startSites, waitForSites, type Scenario, type SitesStatus, type Vehicle } from '../../api';
import { scenarioToRun } from '../planning';
import type { ScenarioOptions } from '../planning/scenarioOptions';

export type SiteSearchParams = {
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
 * Жизненный цикл подбора площадки: сохранение правок → постановка → поллинг с
 * готовыми пробами → итог. Изменённые параметры и ТТХ уходят копией
 * сценария, как у расчёта плана (`scenarioToRun`). Подбор виден, пока выбран
 * тот же сценарий; правка параметров после расчёта помечает его устаревшим.
 */
export function useSiteSearch({
  scenarioId,
  scenario,
  options,
  vehicles,
  vehicleProblems,
}: SiteSearchParams) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<SitesStatus | null>(null);
  const [baseScenarioId, setBaseScenarioId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchedSignature, setSearchedSignature] = useState<string | null>(null);

  const signature = JSON.stringify([scenarioId, options, vehicles]);

  const search = useCallback(async () => {
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
      const searchScenarioId = await scenarioToRun(scenarioId, scenario, options, vehicles);
      const started = await startSites(searchScenarioId);
      setStatus(started);
      const finished = await waitForSites(started.id, setStatus);
      if (finished.state === 'failed') {
        throw new Error(finished.error ?? 'Подбор площадки не удался');
      }
      setSearchedSignature(signature);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [scenarioId, scenario, options, vehicles, vehicleProblems, signature]);

  const visible = status !== null && baseScenarioId === scenarioId;
  return {
    search,
    busy,
    /** Последний опрос или итог; null — подбора нет или выбран другой сценарий. */
    status: visible ? status : null,
    /** Когда начат подбор, миллисекунды эпохи — для времени ожидания. */
    startedAt,
    error,
    /** Параметры менялись после подбора — он про прежние. */
    stale: visible && !busy && searchedSignature !== signature,
  };
}

export type SiteSearchState = ReturnType<typeof useSiteSearch>;
