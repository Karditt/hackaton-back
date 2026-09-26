import { useState } from 'react';
import type { FleetUnit, Vehicle } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { Toggle } from '../../shared/ui/Toggle';
import { ModelSummary } from './ModelSpecs';
import {
  PAYLOAD_KINDS,
  allModels,
  allPayloads,
  boardsFromFleet,
  isEdited,
  newBoard,
  type ModelView,
  type ParkBoard,
} from './park';
import type { ParkState } from './usePark';
import styles from './fleet.module.css';

type Props = {
  vehicles: Vehicle[];
  park: ParkState;
  scenarioId: string;
  scenarioFleet: FleetUnit[] | null;
  /** Перейти к ТТХ модели. */
  onOpenModel: (modelId: string) => void;
};

type Filter = 'all' | 'active' | 'inactive';

const FILTERS: [Filter, string][] = [
  ['all', 'все'],
  ['active', 'в строю'],
  ['inactive', 'не в строю'],
];

/** Борта парка: список с переключателем «в строю» и карточка выбранного борта. */
export function BoardsView({ vehicles, park, scenarioId, scenarioFleet, onOpenModel }: Props) {
  const models = allModels(vehicles, park.park);
  const usable = models.filter((model) => !model.hidden);
  const [selectedId, setSelectedId] = useState('');
  const [newModelId, setNewModelId] = useState('');
  const [filter, setFilter] = useState<Filter>('all');

  const boards = park.park.boards;
  const selected = boards.find((board) => board.id === selectedId) ?? boards[0] ?? null;
  const shown = boards.filter(
    (board) => filter === 'all' || (filter === 'active') === board.active,
  );
  const modelOf = (board: ParkBoard) => models.find((model) => model.id === board.modelId);
  // Выбранная для нового борта своя модель могла быть удалена — тогда первая в списке.
  const newModel = usable.find((model) => model.id === newModelId) ?? usable[0];

  const add = (model: ModelView | undefined) => {
    if (!model) return;
    const board = newBoard(park.park, model);
    park.addBoards([board]);
    setSelectedId(board.id);
  };

  const seedCatalog = () => {
    let current = park.park;
    const added: ParkBoard[] = [];
    for (const model of usable.filter((item) => item.base !== null)) {
      const board = newBoard(current, model);
      added.push(board);
      current = { ...current, boards: [...current.boards, board] };
    }
    park.addBoards(added);
  };

  const seedScenario = () => {
    if (!scenarioFleet) return;
    park.addBoards(boardsFromFleet(park.park, scenarioFleet, vehicles));
  };

  const scenarioCount = (scenarioFleet ?? []).reduce((total, unit) => total + (unit.count ?? 1), 0);

  return (
    <div className={styles.split}>
      <section className={styles.listPane}>
        <div className={styles.toolbar}>
          <select
            className={styles.control}
            aria-label="Модель нового борта"
            value={newModel?.id ?? ''}
            onChange={(event) => setNewModelId(event.target.value)}
          >
            <ModelOptions models={usable} />
          </select>
          <button
            type="button"
            className={styles.primary}
            onClick={() => add(newModel)}
            disabled={newModel === undefined}
          >
            + Добавить борт
          </button>
        </div>

        <div className={styles.filterRow}>
          <div className={styles.chips} role="group" aria-label="Какие борта показать">
            {FILTERS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={filter === id ? styles.chipActive : styles.chip}
                aria-pressed={filter === id}
                onClick={() => setFilter(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {boards.length > 0 && (
            <span className={styles.bulk}>
              <button type="button" className={styles.link} onClick={() => park.setAllActive(true)}>
                все в строй
              </button>
              {' · '}
              <button
                type="button"
                className={styles.link}
                onClick={() => park.setAllActive(false)}
              >
                все из строя
              </button>
            </span>
          )}
        </div>

        {boards.length === 0 ? (
          <div className={styles.emptyList}>
            <p>Парк пуст. Добавьте борт выбранной модели или заполните парк сразу:</p>
            <button
              type="button"
              className={styles.secondary}
              onClick={seedCatalog}
              disabled={vehicles.length === 0}
            >
              По одному борту каждой модели каталога
            </button>
            <button
              type="button"
              className={styles.secondary}
              onClick={seedScenario}
              disabled={scenarioCount === 0}
            >
              Флот сценария {scenarioId ? `«${scenarioId}»` : ''} — {scenarioCount} шт.
            </button>
          </div>
        ) : (
          <ul className={styles.boardList}>
            {shown.map((board) => {
              const model = modelOf(board);
              return (
                <li
                  key={board.id}
                  className={`${styles.boardItem} ${board.id === selected?.id ? styles.selected : ''} ${
                    board.active ? '' : styles.inactive
                  }`}
                >
                  <Toggle
                    checked={board.active}
                    onChange={(active) => park.updateBoard(board.id, { active })}
                    title={
                      board.active ? 'В строю — вывести из строя' : 'Не в строю — ввести в строй'
                    }
                  />
                  <button
                    type="button"
                    className={styles.boardButton}
                    onClick={() => setSelectedId(board.id)}
                  >
                    <span className={styles.boardLabel}>{board.label || 'без имени'}</span>
                    <span className={styles.boardMeta}>
                      {model ? model.name : `${board.modelId} — модели нет`}
                      {model?.base === null && (
                        <span className={styles.userBadge}>своя модель</span>
                      )}
                      {model && isEdited(model) && (
                        <span className={styles.userBadge}>ТТХ правлены</span>
                      )}
                      {model?.hidden && <span className={styles.hiddenBadge}>модель скрыта</span>}
                    </span>
                  </button>
                  <span className={board.active ? styles.stateOn : styles.stateOff}>
                    {board.active ? 'в строю' : 'не в строю'}
                  </span>
                </li>
              );
            })}
            {shown.length === 0 && <li className={styles.muted}>Таких бортов нет.</li>}
          </ul>
        )}
      </section>

      <section className={styles.detailPane}>
        {selected ? (
          <BoardEditor
            key={selected.id}
            board={selected}
            models={models}
            vehicles={vehicles}
            park={park}
            onOpenModel={onOpenModel}
            onRemoved={() => setSelectedId('')}
          />
        ) : (
          <p className={styles.muted}>Выберите борт слева или добавьте новый.</p>
        )}
      </section>
    </div>
  );
}

/** Варианты выбора модели: каталог и свои — отдельными группами. */
function ModelOptions({ models }: { models: ModelView[] }) {
  const catalog = models.filter((model) => model.base !== null);
  const own = models.filter((model) => model.base === null);
  return (
    <>
      <optgroup label="Каталог (data/vehicles.yaml)">
        {catalog.map((model) => (
          <option key={model.id} value={model.id}>
            {model.name}
          </option>
        ))}
      </optgroup>
      {own.length > 0 && (
        <optgroup label="Свои модели">
          {own.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}
            </option>
          ))}
        </optgroup>
      )}
    </>
  );
}

type EditorProps = {
  board: ParkBoard;
  models: ModelView[];
  vehicles: Vehicle[];
  park: ParkState;
  onOpenModel: (modelId: string) => void;
  onRemoved: () => void;
};

/** Карточка борта: имя, модель, нагрузка, в строю ли, АКБ, заметка. */
function BoardEditor({ board, models, vehicles, park, onOpenModel, onRemoved }: EditorProps) {
  const model = models.find((item) => item.id === board.modelId);
  const payloads = allPayloads(vehicles, park.park);
  const own = model ? model.payloadIds : [];
  const update = (patch: Partial<ParkBoard>) => park.updateBoard(board.id, patch);

  const togglePayload = (id: string, checked: boolean) => {
    const rest = board.payloadIds.filter((item) => item !== id);
    update({ payloadIds: checked ? [...rest, id] : rest });
  };

  return (
    <div className={styles.editor}>
      <div className={styles.editorHead}>
        <input
          className={styles.titleInput}
          aria-label="Бортовое имя"
          value={board.label}
          placeholder="Бортовое имя или номер"
          onChange={(event) => update({ label: event.target.value })}
        />
        <Toggle checked={board.active} onChange={(active) => update({ active })}>
          {board.active ? 'В строю' : 'Не в строю'}
        </Toggle>
      </div>
      <p className={styles.hint}>
        {board.active
          ? 'Борт можно брать во флот расчёта.'
          : 'Борт на обслуживании или в резерве — во флот расчёта не попадёт.'}
      </p>

      <label className={styles.field}>
        Модель
        <select
          className={styles.control}
          value={board.modelId}
          onChange={(event) => {
            const next = models.find((item) => item.id === event.target.value);
            // Нагрузка принадлежит модели: при смене модели прежняя невалидна.
            update({
              modelId: event.target.value,
              payloadIds: next ? next.payloadIds.slice(0, 1) : [],
            });
          }}
        >
          {!model && <option value={board.modelId}>{board.modelId} — модели нет</option>}
          {model?.hidden && <option value={model.id}>{model.name} — скрыта</option>}
          <ModelOptions models={models.filter((item) => !item.hidden)} />
        </select>
      </label>

      {model && (
        <div className={styles.modelCard}>
          <ModelSummary model={model} />
          <button type="button" className={styles.link} onClick={() => onOpenModel(model.id)}>
            ТТХ модели полностью →
          </button>
        </div>
      )}
      {model?.hidden && (
        <Notice tone="caution">
          Модель скрыта в разделе «Модели и ТТХ» — такой борт во флот расчёта не попадёт.
        </Notice>
      )}
      {model && (model.base === null || isEdited(model)) && (
        <p className={styles.hint}>
          {model.base === null ? 'Своя модель' : 'ТТХ модели правлены'}: в расчёт уходят числа
          пользователя, план помечает такие борта.
        </p>
      )}

      <fieldset className={styles.fieldset}>
        <legend>Полезная нагрузка на борту</legend>
        {own.length === 0 && <p className={styles.muted}>У модели нет нагрузок.</p>}
        {PAYLOAD_KINDS.map(([kind, label]) => {
          const ofKind = payloads.filter((item) => item.kind === kind && own.includes(item.id));
          if (ofKind.length === 0) return null;
          return (
            <div key={kind} className={styles.payloadGroup}>
              <span className={styles.groupLabel}>{label}</span>
              {ofKind.map((payload) => (
                <label key={payload.id} className={styles.check}>
                  <input
                    type="checkbox"
                    checked={board.payloadIds.includes(payload.id)}
                    onChange={(event) => togglePayload(payload.id, event.target.checked)}
                  />
                  {payload.name}
                  {payload.custom && <span className={styles.userBadge}>своя</span>}
                </label>
              ))}
            </div>
          );
        })}
        <p className={styles.hint}>
          Одна нагрузка — расчёт возьмёт её. Несколько или ни одной — нагрузку выберет модель съёмки
          из всех, что несёт модель.
        </p>
      </fieldset>

      <div className={styles.pair}>
        <label className={styles.field}>
          Комплекты АКБ
          <input
            className={styles.control}
            type="number"
            min={1}
            step={1}
            placeholder="не ограничено"
            value={board.batterySets}
            onChange={(event) => update({ batterySets: event.target.value })}
          />
          <span className={styles.hint}>предел числа вылетов борта</span>
        </label>
        <label className={styles.field}>
          Заметка
          <textarea
            className={styles.control}
            rows={3}
            value={board.note}
            placeholder="серийный номер, налёт, ТО"
            onChange={(event) => update({ note: event.target.value })}
          />
        </label>
      </div>

      <button
        type="button"
        className={styles.danger}
        onClick={() => {
          park.removeBoard(board.id);
          onRemoved();
        }}
      >
        Убрать борт из парка
      </button>
    </div>
  );
}
