import { useEffect, useState } from 'react';
import { getScenarioFiles, type ScenarioFiles } from '../../api';

/**
 * Исходные файлы выбранного сценария (E10): из каких KML и JSON он собран и
 * что с ними сделал импорт. Отчёт импорта считает ядро, фронт его показывает.
 */
export function useScenarioFiles(scenarioId: string) {
  const [files, setFiles] = useState<ScenarioFiles | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setFiles(null);
    setError(null);
    if (!scenarioId) return;
    getScenarioFiles(scenarioId)
      .then((loaded) => {
        if (!cancelled) setFiles(loaded);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(`Файлы сценария не получены: ${cause.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, [scenarioId]);

  return { files, error };
}
