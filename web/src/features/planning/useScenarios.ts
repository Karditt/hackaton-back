import { useEffect, useState } from 'react';
import { listScenarios, type ScenarioSummary } from '../../api';

/** Список сохранённых сценариев, загружается один раз при открытии страницы. */
export function useScenarios() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listScenarios()
      .then((items) => {
        if (!cancelled) setScenarios(items);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(`API недоступен: ${cause.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { scenarios, error };
}
