import type { OptimizationMode, ScenarioSummary } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { ModeSelect } from './ModeSelect';
import { ScenarioSelect } from './ScenarioSelect';
import styles from './PlanningForm.module.css';

type Props = {
  scenarios: ScenarioSummary[];
  scenarioId: string;
  onScenarioChange: (scenarioId: string) => void;
  mode: OptimizationMode;
  onModeChange: (mode: OptimizationMode) => void;
  onSubmit: () => void;
  busy: boolean;
  status: string;
  error: string | null;
};

/** Параметры расчёта и кнопка запуска. */
export function PlanningForm(props: Props) {
  const { busy, scenarioId } = props;
  return (
    <div>
      <ScenarioSelect
        scenarios={props.scenarios}
        value={scenarioId}
        onChange={props.onScenarioChange}
        disabled={busy}
      />
      <ModeSelect value={props.mode} onChange={props.onModeChange} disabled={busy} />

      <button className={styles.submit} onClick={props.onSubmit} disabled={busy || !scenarioId}>
        {busy ? 'Считаю…' : 'Рассчитать'}
      </button>

      <p className={styles.status}>Состояние: {props.status}</p>
      {props.error && <Notice tone="error">{props.error}</Notice>}
    </div>
  );
}
