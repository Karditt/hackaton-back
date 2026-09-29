import { useEffect, useRef } from 'react';
import type { Vehicle } from '../../api';
import { plural } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import { Toggle } from '../../shared/ui/Toggle';
import { gapText } from './gaps';
import { ModelSpecs } from './ModelSpecs';
import {
  PAYLOAD_KINDS,
  VEHICLE_TYPES,
  allModels,
  allPayloads,
  catalogSpecs,
  isEdited,
  modelFromView,
  newModel,
  type ModelView,
  type PayloadView,
} from './park';
import type { ParkState } from './usePark';
import styles from './fleet.module.css';

type Props = {
  vehicles: Vehicle[];
  park: ParkState;
  /** Выбранная модель; пусто — первая в списке. */
  selectedId: string;
  onSelect: (modelId: string) => void;
};

/**
 * Модели и их ТТХ — каталог `data/vehicles.yaml` с правками пользователя.
 *
 * Любую модель каталога можно поправить (числа и состав нагрузок), выключить
 * из работы или скопировать в свою. Правки хранятся отдельно от каталога,
 * поэтому к исходным данным можно вернуться: поле, модель или весь каталог.
 */
export function ModelsView({ vehicles, park, selectedId, onSelect }: Props) {
  const models = allModels(vehicles, park.park);
  const selected = models.find((model) => model.id === selectedId) ?? models[0] ?? null;
  const reserve = vehicles[0]?.battery_reserve_fraction;
  const catalogTouched = Object.keys(park.park.edits).length > 0 || park.park.hidden.length > 0;

  // Карточка другой модели открывается с начала, а не там, где читали прежнюю.
  const detail = useRef<HTMLElement | null>(null);
  useEffect(() => {
    detail.current?.scrollTo({ top: 0 });
  }, [selected?.id]);

  const create = (model = newModel()) => {
    park.addModel(model);
    onSelect(model.id);
  };

  const resetAll = () => {
    const edited = Object.keys(park.park.edits).length;
    const hidden = park.park.hidden.length;
    const message =
      `Вернуть исходный каталог?\n\nПравки ТТХ (${edited}) будут сняты, скрытые модели ` +
      `(${hidden}) вернутся в работу. Свои модели останутся — удалите их отдельно, если не нужны.`;
    if (window.confirm(message)) park.resetAll();
  };

  const group = (custom: boolean, title: string) => {
    const items = models.filter((model) => (model.base === null) === custom);
    return (
      <>
        <h3 className={styles.listHeading}>
          {title} · {items.length}
        </h3>
        <ul className={styles.modelList}>
          {items.map((model) => (
            <li key={model.id}>
              <button
                type="button"
                className={`${styles.modelItem} ${model.id === selected?.id ? styles.selected : ''} ${
                  model.hidden ? styles.inactive : ''
                }`}
                onClick={() => onSelect(model.id)}
              >
                <span className={styles.boardLabel}>
                  {model.name}
                  {isEdited(model) && <span className={styles.userBadge}>изменена</span>}
                  {model.hidden && <span className={styles.hiddenBadge}>скрыта</span>}
                </span>
                <span className={styles.boardMeta}>
                  {VEHICLE_TYPES.find(([type]) => type === model.type)?.[1]} ·{' '}
                  {park.park.boards.filter((board) => board.modelId === model.id).length} в парке
                </span>
              </button>
            </li>
          ))}
          {items.length === 0 && <li className={styles.muted}>Пока нет.</li>}
        </ul>
      </>
    );
  };

  return (
    <div className={styles.split}>
      <section className={styles.listPane}>
        {group(false, 'Каталог')}
        <button
          type="button"
          className={styles.secondary}
          onClick={resetAll}
          disabled={!catalogTouched}
          title={
            catalogTouched
              ? 'Снять все правки ТТХ и вернуть скрытые модели'
              : 'Каталог и так исходный: правок и скрытых моделей нет'
          }
        >
          ↺ Вернуть исходный каталог
        </button>
        {group(true, 'Свои модели')}
        <button type="button" className={styles.secondary} onClick={() => create()}>
          + Новая модель с нуля
        </button>
      </section>

      <section className={styles.detailPane} ref={detail}>
        {selected ? (
          <ModelDetails
            key={selected.id}
            model={selected}
            payloads={allPayloads(vehicles, park.park)}
            park={park}
            reserve={reserve}
            onCopy={() => create(modelFromView(selected))}
            onRemoved={() => onSelect('')}
          />
        ) : (
          <p className={styles.muted}>Каталог техники не загружен.</p>
        )}
      </section>
    </div>
  );
}

type DetailsProps = {
  model: ModelView;
  payloads: PayloadView[];
  park: ParkState;
  reserve: number | undefined;
  onCopy: () => void;
  onRemoved: () => void;
};

/** Карточка модели: ТТХ с правкой, нагрузки, пробелы, возврат к каталогу. */
function ModelDetails({ model, payloads, park, reserve, onCopy, onRemoved }: DetailsProps) {
  const usedBy = park.park.boards.filter((board) => board.modelId === model.id).length;
  const base = model.base;
  const custom = model.custom;

  const setPayloads = (ids: string[]) => {
    if (base) park.editPayloads(base, ids);
    else if (custom) park.updateModel(custom.id, { payloadIds: ids });
  };
  const togglePayload = (id: string, checked: boolean) => {
    const rest = model.payloadIds.filter((item) => item !== id);
    setPayloads(checked ? [...rest, id] : rest);
  };

  return (
    <div className={styles.editor}>
      <div className={styles.editorHead}>
        {custom ? (
          <input
            className={styles.titleInput}
            aria-label="Название модели"
            value={custom.name}
            onChange={(event) => park.updateModel(custom.id, { name: event.target.value })}
          />
        ) : (
          <h2 className={styles.detailTitle}>{model.name}</h2>
        )}
        {base ? (
          <Toggle
            checked={!model.hidden}
            onChange={(shown) => park.setHidden(model.id, !shown)}
            title="Модель в работе: её можно выбрать для борта и флота"
          >
            {model.hidden ? 'Скрыта' : 'В работе'}
          </Toggle>
        ) : (
          <span className={styles.userBadge}>своя модель</span>
        )}
      </div>

      {base && !isEdited(model) && !model.hidden && (
        <p className={styles.hint}>
          Характеристики — из документации производителя; у каждого числа указано, откуда оно. Любое
          число можно поправить: правка хранится отдельно, и к данным каталога всегда можно
          вернуться.
        </p>
      )}
      {base && isEdited(model) && (
        <Notice tone="caution">
          {model.edited.length > 0
            ? `ТТХ изменены: ${model.edited.length} ${plural(model.edited.length, ['поле', 'поля', 'полей'])}`
            : 'Изменён состав нагрузок'}
          {model.edited.length > 0 && model.payloadsEdited ? ' и состав нагрузок' : ''}. В расчёт
          уходят ваши числа, план помечает модель как посчитанную по вашим данным.{' '}
          <button
            type="button"
            className={styles.inlineLink}
            onClick={() => park.resetModel(model.id)}
          >
            Вернуть данные каталога
          </button>
        </Notice>
      )}
      {model.hidden && (
        <Notice tone="caution">
          Модель скрыта: её нет в выборе модели борта и флота, борта этой модели не берутся в
          расчёт. Каталог не изменён — включите переключатель, чтобы вернуть её.
        </Notice>
      )}
      {custom && (
        <>
          <Notice tone="caution">
            ТТХ своей модели — ваши значения, а не документ производителя. В расчёт они уходят
            вместе со сценарием, план помечает такие борта.
          </Notice>
          <div className={styles.pair}>
            <label className={styles.field}>
              Тип
              <select
                className={styles.control}
                value={custom.type}
                onChange={(event) =>
                  park.updateModel(custom.id, { type: event.target.value as typeof custom.type })
                }
              >
                {VEHICLE_TYPES.map(([type, label]) => (
                  <option key={type} value={type}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.field}>
              Источник чисел
              <input
                className={styles.control}
                value={custom.source}
                placeholder="документ, страница, паспорт борта"
                onChange={(event) => park.updateModel(custom.id, { source: event.target.value })}
              />
            </label>
          </div>
        </>
      )}

      <ModelSpecs
        model={model}
        reserveFraction={reserve}
        onChange={(key, value) => {
          if (base) park.editSpec(base, key, value);
          else if (custom)
            park.updateModel(custom.id, { specs: { ...custom.specs, [key]: value } });
        }}
        onReset={base ? (key) => park.editSpec(base, key, catalogSpecs(base)[key]) : undefined}
      />

      <fieldset className={styles.fieldset}>
        <legend>
          Какую нагрузку несёт
          {model.payloadsEdited && (
            <>
              {' '}
              <span className={styles.userBadge}>изменено</span>{' '}
              <button
                type="button"
                className={styles.inlineLink}
                onClick={() =>
                  base &&
                  park.editPayloads(
                    base,
                    (base.payloads ?? []).map((item) => item.id),
                  )
                }
              >
                вернуть
              </button>
            </>
          )}
        </legend>
        {PAYLOAD_KINDS.map(([kind, label]) => {
          const ofKind = payloads.filter((payload) => payload.kind === kind);
          if (ofKind.length === 0) return null;
          return (
            <div key={kind} className={styles.payloadGroup}>
              <span className={styles.groupLabel}>{label}</span>
              {ofKind.map((payload) => (
                <label key={payload.id} className={styles.check}>
                  <input
                    type="checkbox"
                    checked={model.payloadIds.includes(payload.id)}
                    onChange={(event) => togglePayload(payload.id, event.target.checked)}
                  />
                  {payload.name}
                  {payload.custom && <span className={styles.userBadge}>своя</span>}
                </label>
              ))}
            </div>
          );
        })}
      </fieldset>

      {base && (base.gaps ?? []).length > 0 && (
        <details className={styles.gaps}>
          <summary>Чего нет в документах производителя ({base.gaps?.length})</summary>
          <ul>
            {base.gaps?.map((gap) => (
              <li key={gap}>{gapText(gap)}</li>
            ))}
          </ul>
        </details>
      )}

      <div className={styles.actions}>
        <button type="button" className={styles.secondary} onClick={onCopy}>
          Своя модель на основе «{model.name}»
        </button>
        {custom && (
          <button
            type="button"
            className={styles.danger}
            disabled={usedBy > 0}
            title={usedBy > 0 ? `Модель стоит у ${usedBy} бортов парка` : undefined}
            onClick={() => {
              park.removeModel(custom.id);
              onRemoved();
            }}
          >
            Удалить модель
          </button>
        )}
      </div>
      {custom && usedBy > 0 && (
        <p className={styles.hint}>Удалить нельзя: модель стоит у бортов парка ({usedBy}).</p>
      )}
    </div>
  );
}
