/**
 * Корень интерфейса (E10/E11): собирает фичи в раскладку и держит выбор
 * пользователя. Логики расчёта здесь нет — она в features/planning, а все
 * числа приходят с сервера.
 *
 * Редактор сценария (рисование области, зон и площадок мышью) — это E10,
 * его пока нет.
 */

import { useState } from 'react';
import { MapView } from '../features/map/MapView';
import { PlanningForm, usePlanning, useScenarios } from '../features/planning';
import { ResultPanel } from '../features/result';
import { AppHeader } from './AppHeader';
import styles from './App.module.css';

/** Начальный α: оба критерия с равным весом. */
const DEFAULT_ALPHA = 0.5;

export default function App() {
  const { scenarios, error: loadError } = useScenarios();
  const [chosenScenarioId, setChosenScenarioId] = useState('');
  const [alpha, setAlpha] = useState(DEFAULT_ALPHA);

  // Пока пользователь ничего не выбрал, берётся первый сценарий из списка.
  const scenarioId = chosenScenarioId || scenarios[0]?.id || '';
  const planning = usePlanning({ scenarioId, alpha });

  return (
    <div className={styles.layout}>
      <aside className={styles.panel}>
        <AppHeader />
        <PlanningForm
          scenarios={scenarios}
          scenarioId={scenarioId}
          onScenarioChange={setChosenScenarioId}
          alpha={alpha}
          onAlphaChange={setAlpha}
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
