import type { PlanningPhase } from '../features/planning';
import { TABS, type Tab } from './useTab';
import styles from './App.module.css';

type Props = {
  tab: Tab;
  /** Счётчик у вкладки: вылетов в расписании, бортов в строю. */
  badges: Partial<Record<Tab, string>>;
  /** Название выбранного сценария — видно с любой вкладки. */
  scenarioName: string;
  /** Показан план не из расчёта сценария — откуда он: «План подсказки: 3 борта». */
  planLabel?: string;
  /** Состояние расчёта. */
  phase: PlanningPhase;
  /** Подробность состояния — во всплывающей подсказке. */
  status: string;
  /** Сценарий открыт только для просмотра. */
  viewOnly: boolean;
};

/** Состояние расчёта в шапке: подпись и цвет метки. */
const PHASES: Record<PlanningPhase, { label: string; tone: string }> = {
  idle: { label: 'План не рассчитан', tone: 'neutral' },
  running: { label: 'Идёт расчёт', tone: 'running' },
  feasible: { label: 'План готов', tone: 'success' },
  infeasible: { label: 'Задание невыполнимо', tone: 'error' },
  stale: { label: 'Нужен пересчёт', tone: 'warning' },
  error: { label: 'Ошибка расчёта', tone: 'error' },
};

/** Верхняя панель: название, вкладки, выбранный сценарий и состояние расчёта. */
export function AppHeader({
  tab,
  badges,
  scenarioName,
  planLabel,
  phase,
  status,
  viewOnly,
}: Props) {
  const state =
    viewOnly && phase === 'idle' ? { label: 'Только просмотр', tone: 'neutral' } : PHASES[phase];
  return (
    <header className={styles.topbar}>
      <div className={styles.brand}>
        <span className={styles.logo}>GEOSCAN</span>
        <span className={styles.tagline}>Планирование беспилотных авиационных работ</span>
      </div>

      <nav className={styles.tabs} aria-label="Разделы">
        {TABS.map(([id, label]) => (
          <a
            key={id}
            href={`#/${id}`}
            className={id === tab ? styles.tabActive : styles.tab}
            aria-current={id === tab ? 'page' : undefined}
          >
            {label}
            {badges[id] && <span className={styles.tabBadge}>{badges[id]}</span>}
          </a>
        ))}
      </nav>

      <div className={styles.state}>
        {scenarioName && (
          <span className={styles.scenarioName} title={scenarioName}>
            {scenarioName}
          </span>
        )}
        {planLabel && <span className={styles.planLabel}>{planLabel}</span>}
        <span
          className={`${styles.phase} ${styles[`phase_${state.tone}`]}`}
          title={status}
          role="status"
        >
          <span className={styles.phaseDot} aria-hidden="true" />
          {state.label}
        </span>
      </div>
    </header>
  );
}
