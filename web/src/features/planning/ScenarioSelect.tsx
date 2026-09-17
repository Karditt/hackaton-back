import type { ScenarioSummary } from '../../api';
import styles from './PlanningForm.module.css';

type Props = {
  scenarios: ScenarioSummary[];
  value: string;
  onChange: (scenarioId: string) => void;
  disabled: boolean;
};

export function ScenarioSelect({ scenarios, value, onChange, disabled }: Props) {
  return (
    <label className={styles.field}>
      Сценарий
      <select
        className={styles.control}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled || scenarios.length === 0}
      >
        {scenarios.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name || item.id}
          </option>
        ))}
      </select>
    </label>
  );
}
