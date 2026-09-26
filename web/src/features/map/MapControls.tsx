import type { BoardSummary } from '../../api';
import { groupsOf, resetPlanLayers, toggleGroup, type MapFilter } from './layers';
import styles from './MapControls.module.css';

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
    </div>
  );
}
