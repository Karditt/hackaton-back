import styles from './PlanningForm.module.css';

const ALPHA_STEP = 0.05;

type Props = {
  /** Вес makespan в целевой функции, 0..1. */
  value: number;
  onChange: (alpha: number) => void;
  disabled: boolean;
};

/** Выбор α в целевой функции `α·makespan + (1−α)·налёт`. */
export function AlphaSlider({ value, onChange, disabled }: Props) {
  return (
    <label className={styles.field}>
      Критерий оптимальности: α = {value.toFixed(2)}
      <input
        className={styles.control}
        type="range"
        min={0}
        max={1}
        step={ALPHA_STEP}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        disabled={disabled}
      />
      <span className={styles.hint}>
        0 — минимум суммарного налёта, 1 — минимум времени выполнения работ
      </span>
    </label>
  );
}
