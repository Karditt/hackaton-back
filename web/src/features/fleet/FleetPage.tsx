import { useState } from 'react';
import type { FleetUnit, Vehicle } from '../../api';
import { plural } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import { BoardsView } from './BoardsView';
import { ModelsView } from './ModelsView';
import { PayloadsView } from './PayloadsView';
import { allPayloads, parkToFleetRows } from './park';
import type { ParkState } from './usePark';
import styles from './fleet.module.css';

type Props = {
  /** Каталог техники — `GET /vehicles`. */
  vehicles: Vehicle[];
  catalogError: string | null;
  park: ParkState;
  /** Выбранный на вкладке «План» сценарий и его флот — чтобы перенести флот в парк. */
  scenarioId: string;
  scenarioFleet: FleetUnit[] | null;
};

type View = 'boards' | 'models' | 'payloads';

/**
 * Вкладка «Техника» (E10): парк бортов, модели с ТТХ и полезная нагрузка.
 *
 * Флот расчёта набирается отсюда кнопкой в параметрах сценария: берутся
 * борта, которые в строю. ТТХ моделей каталога только показываются —
 * с пометкой, что из документа, что выведено, где пробел (инвариант 6).
 */
export function FleetPage({ vehicles, catalogError, park, scenarioId, scenarioFleet }: Props) {
  const [view, setView] = useState<View>('boards');
  const [modelId, setModelId] = useState('');

  const boards = park.park.boards;
  const active = boards.filter((board) => board.active).length;
  const { rows, skipped } = parkToFleetRows(park.park, vehicles);
  const toCalculation = rows.reduce((total, row) => total + Number(row.count), 0);
  const payloadCount = allPayloads(vehicles, park.park).length;
  const modelCount = vehicles.length + park.park.models.length;
  const edited = Object.keys(park.park.edits).length;
  const hidden = park.park.hidden.length;
  const catalogNote = [
    edited > 0 ? `правлено моделей: ${edited}` : '',
    hidden > 0 ? `скрыто: ${hidden}` : '',
  ]
    .filter(Boolean)
    .join(', ');

  const openModel = (id: string) => {
    setModelId(id);
    setView('models');
  };

  const tabs: [View, string][] = [
    ['boards', `Борта · ${boards.length}`],
    ['models', `Модели и ТТХ · ${modelCount}`],
    ['payloads', `Полезная нагрузка · ${payloadCount}`],
  ];

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Техника</h1>
          <p className={styles.subtitle}>
            В парке {boards.length} {plural(boards.length, ['борт', 'борта', 'бортов'])}, в строю{' '}
            {active}, в расчёт уйдут {toCalculation}
            {skipped.length > 0 &&
              ` (ещё ${skipped.length} — скрытых моделей или с ошибками в ТТХ)`}
            . Флот расчёта набирается на вкладке «План»: «Параметры сценария» → «Взять борта в строю
            из парка».
          </p>
          <p className={styles.subtitle}>
            Каталог моделей — data/vehicles.yaml
            {catalogNote
              ? `; ${catalogNote} — вернуть можно в разделе «Модели и ТТХ»`
              : ', без правок'}
            .
          </p>
        </div>
        <nav className={styles.segmented} aria-label="Разделы техники">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={view === id ? styles.segmentActive : styles.segment}
              aria-pressed={view === id}
              onClick={() => setView(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      {catalogError && <Notice tone="error">{catalogError}</Notice>}
      {!park.persisted && (
        <Notice tone="caution">
          Парк не сохранён ни на сервере, ни в браузере — он проживёт только до перезагрузки
          страницы.
        </Notice>
      )}

      {view === 'boards' && (
        <BoardsView
          vehicles={vehicles}
          park={park}
          scenarioId={scenarioId}
          scenarioFleet={scenarioFleet}
          onOpenModel={openModel}
        />
      )}
      {view === 'models' && (
        <ModelsView vehicles={vehicles} park={park} selectedId={modelId} onSelect={setModelId} />
      )}
      {view === 'payloads' && <PayloadsView vehicles={vehicles} park={park} />}
    </div>
  );
}
