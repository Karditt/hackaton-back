import type { ScenarioSummary } from '../../api';
import styles from './PlanningForm.module.css';

type Props = {
  scenarios: ScenarioSummary[];
  value: string;
  onChange: (scenarioId: string) => void;
  disabled: boolean;
};

export function ScenarioSelect({ scenarios, value, onChange, disabled }: Props) {
  const selected = scenarios.find((item) => item.id === value);
  // Пояснение доступного сценария — что в нём и сколько идёт расчёт. Почему
  // недоступный не считается, форма говорит отдельно, у кнопки расчёта.
  const note = selected?.planning_available !== false ? selected?.planning_note : null;
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
      {note && <span className={styles.hint}>{note}</span>}
    </label>
  );
}
