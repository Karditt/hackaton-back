import { useEffect, useState } from 'react';
import { getScenarioLayers, type ScenarioLayers } from '../../api';

/**
 * Слои выбранного сценария — исходные данные на карте до расчёта (E10).
 * Документ собирает ядро (`GET /scenarios/{id}/layers`), фронт его только
 * рисует и прячет по видам.
 */
export function useScenarioLayers(scenarioId: string) {
  const [layers, setLayers] = useState<ScenarioLayers | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLayers(null);
    setError(null);
    if (!scenarioId) return;
    getScenarioLayers(scenarioId)
      .then((loaded) => {
        if (!cancelled) setLayers(loaded);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(`Слои сценария не получены: ${cause.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, [scenarioId]);

  return { layers, error };
}
