import { useCallback, useEffect, useState } from 'react';
import { getScenario, type Scenario } from '../../api';
import { isEdited, optionsOf, type ScenarioOptions } from './scenarioOptions';

/**
 * Параметры выбранного сценария: загрузка исходного и черновик правок (E10).
 *
 * Исходный сценарий держится рядом с черновиком, чтобы было видно, правлено
 * ли что-нибудь, и чтобы правки можно было отменить, не перечитывая сервер.
 * Геометрия сценария в черновик не попадает — она не редактируется (№8, №12),
 * а переносится в производный сценарий как есть.
 */
export function useScenarioOptions(scenarioId: string, revision = 0) {
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [options, setOptions] = useState<ScenarioOptions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    // Прежний сценарий убирается сразу: иначе, пока грузится новый, расчёт
    // ушёл бы с параметрами предыдущего.
    setScenario(null);
    setOptions(null);
    if (!scenarioId) return;
    getScenario(scenarioId)
      .then((loaded) => {
        if (cancelled) return;
        setScenario(loaded);
        setOptions(optionsOf(loaded));
      })
      .catch((cause: Error) => {
        if (cancelled) return;
        setScenario(null);
        setOptions(null);
        setError(`Сценарий не прочитан: ${cause.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, [scenarioId, revision]);

  /** Вернуть значения сохранённого сценария. */
  const reset = useCallback(() => {
    if (scenario) setOptions(optionsOf(scenario));
  }, [scenario]);

  return {
    scenario,
    options,
    setOptions,
    reset,
    edited: scenario !== null && options !== null && isEdited(scenario, options),
    error,
  };
}
