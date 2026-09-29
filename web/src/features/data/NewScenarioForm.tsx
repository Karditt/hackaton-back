import { useRef, useState } from 'react';
import type { ImportedScenario, LayerStats } from '../../api';
import { decimal, fileSize, plural } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import type { NewScenario, NewScenarioSlot } from './useNewScenario';
import styles from './NewScenario.module.css';

type Slot = {
  id: NewScenarioSlot;
  label: string;
  accept: string;
  /** Что в файле — одной строкой. */
  hint: string;
};

const SLOTS: Slot[] = [
  { id: 'area', label: 'Область съёмки', accept: '.kml', hint: 'полигоны KML — что снять' },
  {
    id: 'zones',
    label: 'Запретные зоны',
    accept: '.kml',
    hint: 'полигоны KML с высотами и временем действия',
  },
  {
    id: 'obstacles',
    label: 'Высотные препятствия',
    accept: '.kml',
    hint: 'точки или контуры KML с высотой',
  },
  {
    id: 'sites',
    label: 'Площадки взлёта и посадки',
    accept: '.kml',
    hint: 'точки KML; «резерв» в имени — резервная',
  },
  {
    id: 'params',
    label: 'Параметры задания',
    accept: '.json',
    hint: 'JSON: тип съёмки, GSD, флот, коридор, сроки',
  },
];

type Props = {
  form: NewScenario;
  /** Имя сценария-образца параметров — выбранного сейчас. */
  templateName: string;
  disabled: boolean;
};

/** Кнопка выбора файла одного вида: сам ввод файла спрятан. */
function SlotRow({ slot, form }: { slot: Slot; form: NewScenario }) {
  const input = useRef<HTMLInputElement | null>(null);
  const file = form.files[slot.id];
  return (
    <li className={styles.slot}>
      <span className={styles.slotText}>
        <span className={styles.slotLabel}>
          {slot.label}
          {slot.id === 'area' && <span className={styles.required}> *</span>}
        </span>
        {file ? (
          <span className={styles.fileLine}>
            <span className={styles.fileName} title={file.name}>
              {file.name}
            </span>
            <span className={styles.muted}>{fileSize(file.size)}</span>
            <button
              type="button"
              className={styles.remove}
              onClick={() => form.pick(slot.id, null)}
              aria-label={`Убрать ${file.name}`}
              title="Убрать файл"
            >
              ×
            </button>
          </span>
        ) : (
          <span className={styles.muted}>{slot.hint}</span>
        )}
      </span>
      <button
        type="button"
        className={styles.choose}
        onClick={() => input.current?.click()}
        disabled={form.busy}
      >
        {file ? 'Заменить' : 'Выбрать'}
      </button>
      <input
        ref={input}
        type="file"
        hidden
        accept={slot.accept}
        onChange={(event) => {
          form.pick(slot.id, event.target.files?.[0] ?? null);
          // Тот же файл можно выбрать повторно.
          event.target.value = '';
        }}
      />
    </li>
  );
}

/** Что будет с видами, файла которых нет, — до нажатия «Создать». */
function consequences(form: NewScenario, templateName: string): string[] {
  const lines: string[] = [];
  if (!form.files.zones) lines.push('Без файла зон — запретных зон нет.');
  if (!form.files.obstacles) lines.push('Без файла препятствий — препятствий нет.');
  if (!form.files.sites) {
    lines.push(
      form.files.params
        ? 'Площадки — из файла параметров; нет и там — места подберёт сервис.'
        : 'Площадок нет — места подберёт сервис: сколько нужно, чтобы охватить область.',
    );
  }
  if (!form.files.params) {
    lines.push(
      `Параметры задания — как у «${templateName}»: тип съёмки, GSD, флот, коридор высот, ` +
        'ветер, сроки. Их можно поменять после создания в панели параметров.',
    );
  }
  return lines;
}

/** Итог импорта одного слоя: «3 зоны из 10, вне области 7». */
function layerLine(label: string, stats: LayerStats | undefined, forms: [string, string, string]) {
  if (!stats || !stats.read) return null;
  const kept = stats.kept ?? 0;
  const parts = [`${kept} ${plural(kept, forms)} из ${stats.read}`];
  if (stats.fixed) parts.push(`исправлено ${stats.fixed}`);
  if (stats.dropped) parts.push(`выброшено ${stats.dropped}`);
  if (stats.skipped) parts.push(`вне области ${stats.skipped}`);
  return `${label}: ${parts.join(', ')}`;
}

/** Отчёт о созданном сценарии: площадь, слои, что сделано с площадками. */
function CreatedReport({ created }: { created: ImportedScenario }) {
  const report = created.report;
  const lines = [
    `Область: ${decimal(report.area_km2 ?? 0, 1)} км², ${report.area?.kept ?? 0} ${plural(
      report.area?.kept ?? 0,
      ['участок', 'участка', 'участков'],
    )}`,
    layerLine('Зоны', report.zones, ['зона', 'зоны', 'зон']),
    layerLine('Препятствия', report.obstacles, ['объект', 'объекта', 'объектов']),
    layerLine('Площадки', report.sites, ['площадка', 'площадки', 'площадок']),
  ].filter(Boolean);
  const warnings = report.warnings ?? [];
  const sitesNote = warnings.find((warning) => warning.code === 'import.sites_auto');
  const rest = warnings.filter((warning) => warning !== sitesNote);
  return (
    <>
      <Notice tone="success">
        Сценарий «{created.name}» создан и выбран. {lines.join('. ')}.
        {sitesNote && <> {sitesNote.message}.</>}
      </Notice>
      {rest.length > 0 && (
        <details className={styles.details}>
          <summary>Замечания импорта ({rest.length})</summary>
          <ul>
            {rest.map((warning, index) => (
              <li key={`${warning.code}-${index}`}>{warning.message}</li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

/**
 * Новый сценарий из своих файлов (E10): кнопка под выбором сценария
 * раскрывает форму — название и файлы по видам. Слои читает ядро, ответ —
 * отчёт импорта; новый сценарий сразу выбирается. Геометрия на карте не
 * рисуется и не правится — её готовят в ГИС и присылают файлами.
 */
export function NewScenarioForm({ form, templateName, disabled }: Props) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <div className={styles.closed}>
        <button
          type="button"
          className={styles.openButton}
          onClick={() => setOpen(true)}
          disabled={disabled}
        >
          + Новый сценарий из своих файлов
        </button>
        {form.created && <CreatedReport created={form.created} />}
      </div>
    );
  }

  return (
    <div className={styles.box}>
      <div className={styles.head}>
        <span className={styles.title}>Новый сценарий</span>
        <button
          type="button"
          className={styles.remove}
          onClick={() => {
            form.reset();
            setOpen(false);
          }}
          aria-label="Закрыть"
          title="Закрыть без создания"
        >
          ×
        </button>
      </div>

      <label className={styles.nameField}>
        Название
        <input
          className={styles.nameInput}
          value={form.name}
          onChange={(event) => form.setName(event.target.value)}
          placeholder="например, «Съёмка полей, октябрь»"
          maxLength={120}
        />
      </label>

      <ul className={styles.slots}>
        {SLOTS.map((slot) => (
          <SlotRow key={slot.id} slot={slot} form={form} />
        ))}
      </ul>

      <ul className={styles.consequences}>
        {consequences(form, templateName).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>

      {form.error && <Notice tone="error">{form.error}</Notice>}

      <button
        type="button"
        className={styles.create}
        onClick={() =>
          void form.create().then((done) => {
            if (done) setOpen(false);
          })
        }
        disabled={form.busy || !form.files.area || disabled}
        title={form.files.area ? undefined : 'Сначала выберите файл области съёмки'}
      >
        {form.busy ? 'Читаю файлы…' : 'Создать сценарий'}
      </button>
      <p className={styles.muted}>
        Формат слоёв — как у файлов любого сценария: их можно скачать в «Исходных данных» на карте и
        взять за образец.
      </p>
    </div>
  );
}
