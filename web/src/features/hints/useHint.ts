import { useCallback, useState } from 'react';
import {
  startHint,
  waitForHint,
  type HintQuestion,
  type HintStatus,
  type Scenario,
  type Vehicle,
} from '../../api';
import { scenarioToRun } from '../planning';
import type { ScenarioOptions } from '../planning/scenarioOptions';

export type HintParams = {
  scenarioId: string;
  /** Сохранённый сценарий; null — ещё не загружен. */
  scenario: Scenario | null;
  /** Параметры панели, возможно правленные; null — ещё не загружены. */
  options: ScenarioOptions | null;
  /** Правленые и свои модели флота с вкладки «Техника» — `Scenario.vehicles`. */
  vehicles: Vehicle[];
  /** Почему часть моделей флота нельзя отдать в расчёт; пусто — можно всё. */
  vehicleProblems: string[];
};

/** Готовый ответ подсказки. */
export type HintAnswer = {
  /** Сценарий, выбранный в момент вопроса. */
  baseScenarioId: string;
  /** Что посчитано: выбранный сценарий или его правленная копия. */
  scenarioId: string;
  question: HintQuestion;
  status: HintStatus;
};

/**
 * Жизненный цикл подсказки по флоту: сохранение правок → вопрос → поллинг →
 * отчёт. Правленые параметры и ТТХ уходят копией сценария, как у расчёта
 * плана (`scenarioToRun`), — подсказка отвечает про то, что видно в панели.
 *
 * Ответ показывается, пока выбран тот же сценарий. Правка параметров после
 * ответа не прячет его, а помечает устаревшим: ответ дорогой (минуты на
 * данных заказчика), и его полезно видеть рядом с правкой.
 */
export function useHint({ scenarioId, scenario, options, vehicles, vehicleProblems }: HintParams) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [tick, setTick] = useState<HintStatus | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<HintAnswer | null>(null);
  const [askedSignature, setAskedSignature] = useState<string | null>(null);

  // Подпись выбора: сценарий, поля панели параметров и ТТХ. Режима здесь нет —
  // прогоны подсказки всегда в режиме «минимум срока».
  const signature = JSON.stringify([scenarioId, options, vehicles]);

  const ask = useCallback(
    async (question: HintQuestion) => {
      if (!scenarioId) return;
      if (vehicleProblems.length > 0) {
        setError(vehicleProblems.join('; '));
        return;
      }
      setBusy(true);
      setError(null);
      setAnswer(null);
      setTick(null);
      setStartedAt(Date.now());
      setStatus('вопрос поставлен в очередь');
      try {
        const hintScenarioId = await scenarioToRun(scenarioId, scenario, options, vehicles, () =>
          setStatus('сохраняю правленые параметры'),
        );
        const hint = await startHint(hintScenarioId, question);
        const finished = await waitForHint(hint.id, (current) => {
          setTick(current);
          setStatus(current.state === 'running' ? 'идут прогоны' : 'вопрос в очереди');
        });
        if (finished.state === 'failed') {
          throw new Error(finished.error ?? 'подсказка не удалась');
        }
        setAnswer({
          baseScenarioId: scenarioId,
          scenarioId: hintScenarioId,
          question,
          status: finished,
        });
        setAskedSignature(signature);
        setStatus('ответ готов');
      } catch (cause) {
        setError((cause as Error).message);
        setStatus('ошибка');
      } finally {
        setBusy(false);
        setTick(null);
      }
    },
    [scenarioId, scenario, options, vehicles, vehicleProblems, signature],
  );

  const visible = answer !== null && answer.baseScenarioId === scenarioId;
  return {
    ask,
    busy,
    status,
    /** Последний опрос идущей подсказки; null — не идёт. */
    tick,
    /** Когда задан вопрос, миллисекунды эпохи — для времени ожидания. */
    startedAt,
    error,
    answer: visible ? answer : null,
    /** Параметры правились после ответа — ответ про прежние. */
    stale: visible && askedSignature !== signature,
  };
}

export type HintState = ReturnType<typeof useHint>;
