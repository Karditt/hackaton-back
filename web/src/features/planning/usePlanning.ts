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
  type Vehicle,
} from '../../api';
import { applyOptions, isEdited, sameVehicles, type ScenarioOptions } from './scenarioOptions';

export type PlanningParams = {
  scenarioId: string;
  /** Режим оптимизации: минимум срока или минимум ресурса (заказчик, №23). */
  mode: OptimizationMode;
  /** Сохранённый сценарий; null — ещё не загружен. */
  scenario: Scenario | null;
  /** Параметры панели, возможно правленные; null — ещё не загружены. */
  options: ScenarioOptions | null;
  /** Правленые и свои модели флота с вкладки «Техника» — `Scenario.vehicles`. */
  vehicles: Vehicle[];
  /** Почему часть моделей флота нельзя отдать в расчёт; пусто — можно всё. */
  vehicleProblems: string[];
};

/** Завершённый расчёт. */
export type PlanningRun = {
  /** Что посчитано: выбранный сценарий или его правленная копия. */
  scenarioId: string;
  mode: OptimizationMode;
  jobId: string;
  result: PipelineResult;
  document: GeoJsonDocument;
  /** Откуда план, если не из расчёта сценария: «План подсказки: 5 бортов». */
  label?: string;
  /** Чей это готовый план: ответ подсказки или сохранённый расчёт из истории. */
  origin?: PlanOrigin;
};

/**
 * Готовый план, показанный без расчёта: подсказка по флоту, фронт «срок —
 * ресурс», подбор площадки, история.
 */
export type PlanOrigin = 'hint' | 'pareto' | 'sites' | 'history';

/**
 * Состояние расчёта для показа: не считали, идёт, готов (выполним или нет),
 * устарел (параметры правились после расчёта), ошибка.
 */
export type PlanningPhase = 'idle' | 'running' | 'feasible' | 'infeasible' | 'stale' | 'error';

/**
 * Какой сценарий уходит в расчёт: выбранный или, если параметры панели или ТТХ
 * правлены, его копия `<id>--custom` — она сначала сохраняется. Общая точка
 * для расчёта плана и подсказки по флоту.
 *
 * @param onSaving вызывается перед сохранением копии — для строки состояния
 * @returns идентификатор сценария для расчёта
 */
export async function scenarioToRun(
  scenarioId: string,
  scenario: Scenario | null,
  options: ScenarioOptions | null,
  vehicles: Vehicle[],
  onSaving?: () => void,
): Promise<string> {
  if (!scenario || !options) return scenarioId;
  if (!isEdited(scenario, options) && sameVehicles(scenario, vehicles)) return scenarioId;
  onSaving?.();
  return (await saveScenario(applyOptions(scenario, options, vehicles))).id;
}

/**
 * Жизненный цикл расчёта: сохранение правок → постановка → поллинг → результат.
 *
 * Правленые параметры и ТТХ уходят отдельным сценарием (`POST /scenarios`), и
 * считается он — исходный файл остаётся нетронутым. Проверку содержания
 * делает сервер: недопустимое значение возвращается как 422 с причиной и
 * показывается пользователем, а не чинится молча (заказчик, №19).
 *
 * Результат отдаётся наружу, только пока выбранные параметры совпадают с
 * теми, с которыми он посчитан. Иначе метрики прошлого расчёта легко принять
 * за текущие (долг №18 в docs/current_state.md).
 */
export function usePlanning({
  scenarioId,
  mode,
  scenario,
  options,
  vehicles,
  vehicleProblems,
}: PlanningParams) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('план не рассчитан');
  const [phase, setPhase] = useState<PlanningPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<PlanningRun | null>(null);
  const [ranSignature, setRanSignature] = useState<string | null>(null);

  // Подпись выбора: сценарий, режим, все поля панели параметров и ТТХ флота.
  const signature = JSON.stringify([scenarioId, mode, options, vehicles]);

  const start = useCallback(async () => {
    if (!scenarioId) return;
    if (vehicleProblems.length > 0) {
      setError(vehicleProblems.join('; '));
      setStatus('ошибка');
      setPhase('error');
      return;
    }
    setBusy(true);
    setPhase('running');
    setError(null);
    setRun(null);
    setStatus('расчёт поставлен в очередь');
    try {
      const planId = await scenarioToRun(scenarioId, scenario, options, vehicles, () =>
        setStatus('сохраняю изменённые параметры'),
      );
      const job = await startPlanning(planId, mode);
      const finished = await waitForJob(job.id, (tick) =>
        setStatus(tick.state === 'running' ? 'идёт расчёт' : 'расчёт в очереди'),
      );
      if (finished.state === 'failed') {
        throw new Error(finished.error ?? 'расчёт не удался');
      }
      const [result, document] = await Promise.all([
        getResult(job.id),
        fetchMissionGeoJson(job.id),
      ]);
      const feasible = result.mission.status === 'feasible';
      setRun({ scenarioId: planId, mode, jobId: job.id, result, document });
      setRanSignature(signature);
      setStatus(feasible ? 'план готов' : 'задание невыполнимо');
      setPhase(feasible ? 'feasible' : 'infeasible');
    } catch (cause) {
      setError((cause as Error).message);
      setStatus('ошибка');
      setPhase('error');
    } finally {
      setBusy(false);
    }
  }, [scenarioId, mode, scenario, options, vehicles, vehicleProblems, signature]);

  /**
   * Показать готовый план как текущий расчёт — без нового расчёта. Так
   * показывается план из ответа подсказки: сервер уже посчитал его и завёл
   * обычной задачей, здесь только загрузка результата и выгрузки.
   *
   * Вызывающий отвечает за то, что план посчитан по текущим параметрам:
   * план считается действующим, пока выбор не изменится.
   *
   * @param jobId задача расчёта с готовым планом
   * @param label откуда план — показывается в шапке и над результатом
   * @param origin подсказка по флоту или история расчётов
   */
  const showJob = useCallback(
    async (jobId: string, label: string, origin: PlanOrigin = 'hint') => {
      setBusy(true);
      setPhase('running');
      setError(null);
      setRun(null);
      setStatus('загружаю готовый план');
      try {
        const [result, document] = await Promise.all([
          getResult(jobId),
          fetchMissionGeoJson(jobId),
        ]);
        const feasible = result.mission.status === 'feasible';
        setRun({
          scenarioId: result.scenario_id,
          mode: result.mission.mode,
          jobId,
          result,
          document,
          label,
          origin,
        });
        setRanSignature(signature);
        setStatus(feasible ? `${label} — готов` : `${label} — задание невыполнимо`);
        setPhase(feasible ? 'feasible' : 'infeasible');
      } catch (cause) {
        setError((cause as Error).message);
        setStatus('ошибка');
        setPhase('error');
      } finally {
        setBusy(false);
      }
    },
    [signature],
  );

  const isCurrent = run !== null && ranSignature === signature;
  const stale = run !== null && !isCurrent;

  return {
    start,
    showJob,
    busy,
    status: stale ? 'параметры изменены — нужен пересчёт' : status,
    phase: stale ? ('stale' as const) : phase,
    error,
    run: isCurrent ? run : null,
  };
}
