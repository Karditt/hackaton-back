import { useState } from 'react';
import type { BoardSummary, ScenarioFiles, ScenarioLayers } from '../api';
import { FileManager, type DataKindId, type UploadedFile } from '../features/data';
import { MapControls } from '../features/map/MapControls';
import { LAYER_GROUPS, type MapFilter } from '../features/map/layers';
import { Notice } from '../shared/ui/Notice';
import styles from './App.module.css';

type Props = {
  scenarioId: string;
  layers: ScenarioLayers | null;
  files: ScenarioFiles | null;
  layersError: string | null;
  filter: MapFilter;
  onFilterChange: (filter: MapFilter) => void;
  uploads: UploadedFile[];
  onUpload: (kind: DataKindId, files: FileList | null) => void;
  onRemoveUpload: (id: string) => void;
  /** Борта рассчитанного плана; null — плана нет. */
  boards: BoardSummary[] | null;
};

/** Значок «слои»: три стопкой. Рисуется цветом текста кнопки. */
function LayersIcon() {
  return (
    <svg
      className={styles.overlayIcon}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3 2 8l10 5 10-5-10-5Z" />
      <path d="m2 12 10 5 10-5" />
      <path d="m2 16 10 5 10-5" />
    </svg>
  );
}

/**
 * Панель поверх карты: менеджер исходных данных и слои результата расчёта.
 * Стоит рядом с картой, потому что её переключатели меняют именно карту;
 * сворачивается в заметную кнопку, чтобы не закрывать область.
 */
export function MapOverlay(props: Props) {
  const [open, setOpen] = useState(true);
  const hidden = LAYER_GROUPS.filter((group) => !props.filter.groups.includes(group.id)).length;

  if (!open) {
    return (
      <button
        type="button"
        className={styles.overlayOpen}
        onClick={() => setOpen(true)}
        aria-expanded={false}
      >
        <LayersIcon />
        Данные и слои
        {hidden > 0 && (
          <span className={styles.overlayCount} title="Сколько групп слоёв скрыто на карте">
            скрыто {hidden}
          </span>
        )}
      </button>
    );
  }

  return (
    <aside className={styles.overlay} aria-label="Данные и слои карты">
      <div className={styles.overlayHead}>
        <h2 className={styles.overlayTitle}>
          <LayersIcon />
          Данные и слои
        </h2>
        <button
          type="button"
          className={styles.overlayClose}
          onClick={() => setOpen(false)}
          aria-label="Свернуть панель"
          aria-expanded={true}
          title="Свернуть"
        >
          ×
        </button>
      </div>

      <section className={styles.overlaySection}>
        <h3 className={styles.overlayHeading}>Исходные данные</h3>
        {props.layersError && <Notice tone="error">{props.layersError}</Notice>}
        <FileManager
          scenarioId={props.scenarioId}
          layers={props.layers}
          files={props.files}
          filter={props.filter}
          onFilterChange={props.onFilterChange}
          uploads={props.uploads}
          onUpload={props.onUpload}
          onRemove={props.onRemoveUpload}
        />
      </section>

      <section className={styles.overlaySection}>
        <h3 className={styles.overlayHeading}>Результат расчёта</h3>
        {props.boards ? (
          <MapControls
            filter={props.filter}
            onChange={props.onFilterChange}
            boards={props.boards}
          />
        ) : (
          <p className={styles.overlayEmpty}>
            Появится после расчёта: подобласти, маршрут по фазам, ключевые точки, выбор борта.
          </p>
        )}
      </section>
    </aside>
  );
}
