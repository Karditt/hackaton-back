/**
 * Корень интерфейса (E10/E11): собирает фичи в раскладку и держит выбор
 * пользователя. Логики расчёта здесь нет — она в features/planning, а все
 * числа приходят с сервера.
 *
 * Ввода и редактирования геометрии на карте нет и не будет: заказчик это
 * исключил (docs/customer_qa.md, №8, №12). Сценарии приходят файлами через
 * API; правятся только параметры задания (E10), загрузка файлов — долг №29.
 */

import { useEffect, useState } from 'react';
import type { OptimizationMode } from '../api';
import { MapControls } from '../features/map/MapControls';
import { MapView } from '../features/map/MapView';
import { allLayers, type MapFilter } from '../features/map/layers';
import {
  PlanningForm,
  usePlanning,
  useScenarioOptions,
  useScenarios,
  useVehicles,
} from '../features/planning';
import { ResultPanel } from '../features/result';
import { Section } from '../shared/ui/Section';
import { AppHeader } from './AppHeader';
import styles from './App.module.css';

/** Режим по умолчанию — тот же, что в core/schemas.py. */
const DEFAULT_MODE: OptimizationMode = 'min_makespan';

export default function App() {
  const { scenarios, rejected, error: loadError } = useScenarios();
  const { vehicles, error: catalogError } = useVehicles();
  const [chosenScenarioId, setChosenScenarioId] = useState('');
  const [mode, setMode] = useState<OptimizationMode>(DEFAULT_MODE);
  const [filter, setFilter] = useState<MapFilter>(allLayers);

  // Пока пользователь ничего не выбрал, берётся первый сценарий из списка.
  const scenarioId = chosenScenarioId || scenarios[0]?.id || '';
  const draft = useScenarioOptions(scenarioId);
  const planning = usePlanning({
    scenarioId,
    mode,
    scenario: draft.scenario,
    options: draft.options,
  });

  // Новый расчёт — новый состав бортов, поэтому отбор возвращается к «показать всё».
  const jobId = planning.run?.jobId;
  useEffect(() => {
    setFilter(allLayers());
  }, [jobId]);

  return (
    <div className={styles.layout}>
      <aside className={styles.panel}>
        <AppHeader />
        <PlanningForm
          scenarios={scenarios}
          rejected={rejected}
          scenarioId={scenarioId}
          onScenarioChange={setChosenScenarioId}
          mode={mode}
          onModeChange={setMode}
          scenario={draft.scenario}
          options={draft.options}
          onOptionsChange={draft.setOptions}
          onOptionsReset={draft.reset}
          optionsEdited={draft.edited}
          vehicles={vehicles}
          onSubmit={planning.start}
          busy={planning.busy}
          status={planning.status}
          error={loadError ?? catalogError ?? draft.error ?? planning.error}
        />
        {planning.run && (
          <>
            <Section title="Слои карты">
              <MapControls
                filter={filter}
                onChange={setFilter}
                boards={planning.run.document.properties?.boards ?? []}
              />
            </Section>
            <ResultPanel run={planning.run} />
          </>
        )}
      </aside>

      <main className={styles.map}>
        <MapView mission={planning.run?.document ?? null} filter={filter} />
      </main>
    </div>
  );
}
