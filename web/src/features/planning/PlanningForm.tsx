import type { ScenarioSummary } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { AlphaSlider } from './AlphaSlider';
import { ScenarioSelect } from './ScenarioSelect';
import styles from './PlanningForm.module.css';

type Props = {
  scenarios: ScenarioSummary[];
  scenarioId: string;
  onScenarioChange: (scenarioId: string) => void;
  alpha: number;
  onAlphaChange: (alpha: number) => void;
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
      <AlphaSlider value={props.alpha} onChange={props.onAlphaChange} disabled={busy} />

      <button className={styles.submit} onClick={props.onSubmit} disabled={busy || !scenarioId}>
        {busy ? 'Считаю…' : 'Рассчитать'}
      </button>

      <p className={styles.status}>Состояние: {props.status}</p>
      {props.error && <Notice tone="error">{props.error}</Notice>}
    </div>
  );
}
