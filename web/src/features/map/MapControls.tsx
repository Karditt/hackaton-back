import type { BoardSummary } from '../../api';
import { LAYER_GROUPS, allLayers, type MapFilter } from './layers';
import styles from './MapControls.module.css';

type Props = {
  filter: MapFilter;
  onChange: (filter: MapFilter) => void;
  /** Борта плана — из свойства `boards` выгрузки GeoJSON. */
  boards: BoardSummary[];
};

/**
 * Что показано на карте: галочки по видам объектов и выбор борта (E11).
 *
 * Карта рисует все слои сразу, и на плане в сотню вылетов один маршрут в них
 * теряется. Отбор — только показ: план не пересчитывается и выгрузка не
 * меняется.
 */
export function MapControls({ filter, onChange, boards }: Props) {
  const toggle = (id: string) => {
    const groups = filter.groups.includes(id)
      ? filter.groups.filter((item) => item !== id)
      : [...filter.groups, id];
    onChange({ ...filter, groups });
  };

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
        {LAYER_GROUPS.map((group) => (
          <li key={group.id}>
            <label className={styles.item}>
              <input
                type="checkbox"
                checked={filter.groups.includes(group.id)}
                onChange={() => toggle(group.id)}
              />
              {group.label}
            </label>
          </li>
        ))}
      </ul>

      <button type="button" className={styles.reset} onClick={() => onChange(allLayers())}>
        показать всё
      </button>
    </div>
  );
}
