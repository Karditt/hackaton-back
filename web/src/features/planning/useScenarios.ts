import { useCallback, useEffect, useState } from 'react';
import { listScenarios, type RejectedScenario, type ScenarioSummary } from '../../api';

/**
 * Список сохранённых сценариев: загружается при открытии страницы и заново —
 * когда сценарий собран из загруженных файлов (`reload`). Вместе с ним —
 * файлы, которые сервер не смог прочитать: их не прячут.
 */
export function useScenarios() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [rejected, setRejected] = useState<RejectedScenario[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    listScenarios()
      .then((listing) => {
        if (cancelled) return;
        setScenarios(listing.scenarios);
        setRejected(listing.rejected);
        setError(null);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(`API недоступен: ${cause.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, [version]);

  const reload = useCallback(() => setVersion((current) => current + 1), []);

  return { scenarios, rejected, error, reload };
}
