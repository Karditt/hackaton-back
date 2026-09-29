import type { BoardSummary, FlightPhase } from '../../api';
import { groupsOf, resetPlanLayers, toggleGroup, type MapFilter } from './layers';
import { PHASE_COLORS } from './missionStyles';
import { PHASE_LABELS } from './popup';
import styles from './MapControls.module.css';

/** Фазы в легенде — в порядке полёта; ожидание в воздухе в планах не встречается. */
const LEGEND_PHASES: FlightPhase[] = [
  'takeoff',
  'climb',
  'transit',
  'strip',
  'turn',
  'descent',
  'landing',
];

type Props = {
  filter: MapFilter;
  onChange: (filter: MapFilter) => void;
  /** Борта плана — из свойства `boards` выгрузки GeoJSON. */
  boards: BoardSummary[];
};

/**
 * Слои результата расчёта и выбор борта (E11). Исходные данные — область,
 * зоны, препятствия, площадки — переключаются в менеджере данных.
 *
 * Карта рисует все слои сразу, и на плане в сотню вылетов один маршрут в них
 * теряется. Отбор — только показ: план не пересчитывается и выгрузка не
 * меняется.
 */
export function MapControls({ filter, onChange, boards }: Props) {
  return (
    <div>
      <label className={styles.board}>
        Борт
        <select
          className={styles.select}
          value={filter.board}
          onChange={(event) => onChange({ ...filter, board: event.target.value })}
        >
          <option value="">все борта</option>
          {boards.map((board) => (
            <option key={board.board_id} value={board.board_id}>
              {board.label} — {board.sortie_count} выл.
            </option>
          ))}
        </select>
      </label>

      <ul className={styles.list}>
        {groupsOf('plan').map((group) => (
          <li key={group.id}>
            <label className={styles.item}>
              <input
                type="checkbox"
                checked={filter.groups.includes(group.id)}
                onChange={(event) => onChange(toggleGroup(filter, group.id, event.target.checked))}
              />
              {group.label}
            </label>
          </li>
        ))}
      </ul>

      <button
        type="button"
        className={styles.reset}
        onClick={() => onChange(resetPlanLayers(filter))}
      >
        показать всё
      </button>

      <ul className={styles.legend} aria-label="Цвета маршрута">
        {LEGEND_PHASES.map((phase) => (
          <li key={phase} className={styles.legendItem}>
            <span
              className={phase === 'strip' ? styles.swatchWide : styles.swatch}
              style={{ background: PHASE_COLORS[phase] }}
              aria-hidden="true"
            />
            {PHASE_LABELS[phase]}
          </li>
        ))}
      </ul>
    </div>
  );
}
