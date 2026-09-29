import type { OptimizationMode } from '../../api';
import styles from './PlanningForm.module.css';

const MODES: [mode: OptimizationMode, label: string, hint: string][] = [
  ['min_makespan', 'Минимум срока', 'кратчайшее время выполнения всех работ'],
  ['min_resource', 'Минимум ресурса', 'меньше налёта и циклов взлёт-посадка'],
];

type Props = {
  value: OptimizationMode;
  onChange: (mode: OptimizationMode) => void;
  disabled: boolean;
};

/** Выбор режима оптимизации (заказчик, №23; docs/dev/PROJECT_BRIEF.md §4.3). */
export function ModeSelect({ value, onChange, disabled }: Props) {
  return (
    <fieldset className={styles.field}>
      <legend>Критерий оптимальности</legend>
      {MODES.map(([mode, label, hint]) => (
        <label key={mode} className={styles.option}>
          <input
            type="radio"
            name="mode"
            value={mode}
            checked={value === mode}
            onChange={() => onChange(mode)}
            disabled={disabled}
          />
          {label}
          <span className={styles.hint}>{hint}</span>
        </label>
      ))}
    </fieldset>
  );
}
