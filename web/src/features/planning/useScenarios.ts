import { useEffect, useState } from 'react';
import { listScenarios, type RejectedScenario, type ScenarioSummary } from '../../api';

/**
 * Список сохранённых сценариев, загружается один раз при открытии страницы.
 * Вместе с ним — файлы, которые сервер не смог прочитать: их не прячут.
 */
export function useScenarios() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [rejected, setRejected] = useState<RejectedScenario[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listScenarios()
      .then((listing) => {
        if (cancelled) return;
        setScenarios(listing.scenarios);
        setRejected(listing.rejected);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(`API недоступен: ${cause.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { scenarios, rejected, error };
}
