import type {
  OptimizationMode,
  RejectedScenario,
  Scenario,
  ScenarioSummary,
  Vehicle,
} from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { ModeSelect } from './ModeSelect';
import { ScenarioOptionsForm } from './ScenarioOptionsForm';
import { ScenarioSelect } from './ScenarioSelect';
import type { ScenarioOptions } from './scenarioOptions';
import styles from './PlanningForm.module.css';

type Props = {
  scenarios: ScenarioSummary[];
  /** Файлы сценариев, которые сервер не прочитал, — с причиной (долг №28). */
  rejected: RejectedScenario[];
  scenarioId: string;
  onScenarioChange: (scenarioId: string) => void;
  mode: OptimizationMode;
  onModeChange: (mode: OptimizationMode) => void;
  /** Выбранный сценарий целиком; null — ещё не загружен. */
  scenario: Scenario | null;
  options: ScenarioOptions | null;
  onOptionsChange: (options: ScenarioOptions) => void;
  onOptionsReset: () => void;
  optionsEdited: boolean;
  vehicles: Vehicle[];
  onSubmit: () => void;
  busy: boolean;
  status: string;
  error: string | null;
};

/** Параметры расчёта и кнопка запуска. */
export function PlanningForm(props: Props) {
  const { busy, scenarioId, scenario, options } = props;
  return (
    <div>
      <ScenarioSelect
        scenarios={props.scenarios}
        value={scenarioId}
        onChange={props.onScenarioChange}
        disabled={busy}
      />
      {props.rejected.length > 0 && (
        <Notice tone="caution">
          Не прочитаны файлы сценариев ({props.rejected.length}):{' '}
          {props.rejected.map((item) => `${item.file} — ${item.reason}`).join('; ')}
        </Notice>
      )}
      <ModeSelect value={props.mode} onChange={props.onModeChange} disabled={busy} />

      {scenario && options && (
        <ScenarioOptionsForm
          scenario={scenario}
          options={options}
          onChange={props.onOptionsChange}
          onReset={props.onOptionsReset}
          edited={props.optionsEdited}
          vehicles={props.vehicles}
          disabled={busy}
        />
      )}

      <button
        className={styles.submit}
        onClick={props.onSubmit}
        // Пока параметры не загружены, расчёт ушёл бы без правок панели.
        disabled={busy || !scenarioId || options === null}
      >
        {busy ? 'Считаю…' : 'Рассчитать'}
      </button>

      <p className={styles.status}>Состояние: {props.status}</p>
      {props.error && <Notice tone="error">{props.error}</Notice>}
    </div>
  );
}
