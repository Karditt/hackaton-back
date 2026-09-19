/**
 * Корень интерфейса (E10/E11): собирает фичи в раскладку и держит выбор
 * пользователя. Логики расчёта здесь нет — она в features/planning, а все
 * числа приходят с сервера.
 *
 * Ввода и редактирования геометрии на карте нет и не будет: заказчик это
 * исключил (docs/customer_qa.md, №8, №12). Сценарии приходят файлами через
 * API; загрузка файлов в вебе — E10.
 */

import { useState } from 'react';
import type { OptimizationMode } from '../api';
import { MapView } from '../features/map/MapView';
import { PlanningForm, usePlanning, useScenarios } from '../features/planning';
import { ResultPanel } from '../features/result';
import { AppHeader } from './AppHeader';
import styles from './App.module.css';

/** Режим по умолчанию — тот же, что в core/schemas.py. */
const DEFAULT_MODE: OptimizationMode = 'min_makespan';

export default function App() {
  const { scenarios, rejected, error: loadError } = useScenarios();
  const [chosenScenarioId, setChosenScenarioId] = useState('');
  const [mode, setMode] = useState<OptimizationMode>(DEFAULT_MODE);

  // Пока пользователь ничего не выбрал, берётся первый сценарий из списка.
  const scenarioId = chosenScenarioId || scenarios[0]?.id || '';
  const planning = usePlanning({ scenarioId, mode });

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
          onSubmit={planning.start}
          busy={planning.busy}
          status={planning.status}
          error={loadError ?? planning.error}
        />
        {planning.run && <ResultPanel run={planning.run} />}
      </aside>

      <main className={styles.map}>
        <MapView mission={planning.run?.document ?? null} />
      </main>
    </div>
  );
}
