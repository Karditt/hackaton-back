import { useRef } from 'react';
import {
  scenarioFileUrl,
  type LayerStats,
  type ScenarioFile,
  type ScenarioFiles,
  type ScenarioLayers,
} from '../../api';
import { decimal, fileSize, plural } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import { Toggle } from '../../shared/ui/Toggle';
import { toggleGroup, type MapFilter } from '../map/layers';
import { DATA_KINDS, type DataKind } from './dataKinds';
import type { Uploads } from './useUploads';
import styles from './FileManager.module.css';

type Props = {
  /** Выбранный сценарий — откуда сейчас взяты исходные данные. */
  scenarioId: string;
  /** Слои сценария с числом объектов по видам; null — ещё не получены. */
  layers: ScenarioLayers | null;
  /** Исходные файлы сценария; null — ещё не получены. */
  files: ScenarioFiles | null;
  filter: MapFilter;
  onFilterChange: (filter: MapFilter) => void;
  uploads: Uploads;
};

/** Что возьмётся у выбранного сценария, если файла вида не загрузили. */
function borrowedNote(uploads: Uploads): string | null {
  const kinds = new Set(uploads.files.map((file) => file.kind));
  if (kinds.has('scenario')) return null;
  const json = uploads.files.some(
    (file) =>
      ['params', 'site', 'airspace'].includes(file.kind) &&
      file.name.toLowerCase().endsWith('.json'),
  );
  const missing = [
    !kinds.has('area') && 'область',
    !kinds.has('no_fly_zone') && 'зоны',
    !kinds.has('obstacle') && 'препятствия',
    !json && 'параметры задания',
  ].filter(Boolean);
  if (missing.length === 0) return null;
  return `Из выбранного сценария возьмутся: ${missing.join(', ')}.`;
}

/** Сколько объектов вида в сценарии — подпись раздела. */
function describeCount(kind: DataKind, layers: ScenarioLayers | null): string {
  if (kind.countKey === null) return kind.hint;
  if (layers === null) return 'загрузка…';
  const count = layers.properties.counts[kind.countKey] ?? 0;
  if (count === 0) return 'в сценарии нет';
  const text = `${count} ${plural(count, kind.countForms)}`;
  const area = layers.properties.area_km2;
  return kind.id === 'area' && area !== null ? `${text} · ${decimal(area, 1)} км²` : text;
}

/** Что импорт сделал со слоем: «прочитано 696 · принято 549 · выброшено 135». */
function describeStats(stats: LayerStats): string {
  const parts = [`прочитано ${stats.read ?? 0}`, `принято ${stats.kept ?? 0}`];
  if (stats.fixed) parts.push(`исправлено ${stats.fixed}`);
  if (stats.dropped) parts.push(`выброшено ${stats.dropped}`);
  if (stats.skipped) parts.push(`вне области ${stats.skipped}`);
  return parts.join(' · ');
}

/** Кнопка выбора файла: сам ввод файла спрятан, кнопка доступна с клавиатуры. */
function UploadButton({ kind, onPick }: { kind: DataKind; onPick: Uploads['add'] }) {
  const input = useRef<HTMLInputElement | null>(null);
  return (
    <>
      <button
        type="button"
        className={styles.upload}
        onClick={() => input.current?.click()}
        title={`Загрузить: ${kind.label.toLowerCase()} (${kind.formats})`}
      >
        Загрузить
      </button>
      <input
        ref={input}
        type="file"
        hidden
        multiple
        accept={kind.accept}
        onChange={(event) => {
          onPick(kind.id, event.target.files);
          // Тот же файл можно выбрать повторно.
          event.target.value = '';
        }}
      />
    </>
  );
}

/** Исходный файл сценария: имя со ссылкой на скачивание, размер, итог импорта. */
function SourceFile({
  scenarioId,
  file,
  shared,
}: {
  scenarioId: string;
  file: ScenarioFile;
  /** Данные лежат в общем файле (площадки, граница — в параметрах задания). */
  shared: boolean;
}) {
  return (
    <li className={styles.source}>
      <span className={styles.sourceLine}>
        {shared && <span className={styles.sourcePrefix}>в файле</span>}
        <a
          className={styles.sourceName}
          href={scenarioFileUrl(scenarioId, file.kind)}
          title={`Скачать ${file.name}`}
        >
          {file.name}
        </a>
        <span className={styles.fileSize}>{fileSize(file.size_bytes)}</span>
      </span>
      {file.stats && !shared && <span className={styles.stats}>{describeStats(file.stats)}</span>}
    </li>
  );
}

/**
 * Менеджер исходных данных (E10): файлы по видам — область, зоны,
 * препятствия, площадки, параметры — и показ каждого вида на карте.
 *
 * У каждого вида — исходный файл выбранного сценария: слой KML в формате
 * заказчика или файл параметров, со ссылкой на скачивание и итогом импорта.
 * Кнопки «Загрузить» копят свои файлы, «Собрать сценарий» отправляет их
 * серверу (`useUploads`): слои читает ядро, отвечает отчётом импорта, новый
 * сценарий сразу выбирается. Геометрия не рисуется и не правится — заказчик
 * это исключил.
 */
export function FileManager(props: Props) {
  const { layers, files, filter, uploads } = props;
  const pending = uploads.files.length;
  const borrowed = borrowedNote(uploads);
  const sourceId = files?.source_id ?? null;
  const byKind = new Map((files?.files ?? []).map((file) => [file.kind, file]));
  const warnings = files?.warnings ?? [];

  return (
    <div>
      <p className={styles.origin}>
        {files === null
          ? 'Файлы сценария загружаются…'
          : sourceId === null
            ? 'Сценарий сохранён без исходных файлов — данные взяты из него самого.'
            : sourceId === props.scenarioId
              ? 'Исходные файлы сценария: слои KML и параметры задания'
              : 'Копия с изменёнными параметрами; файлы — исходного сценария'}
      </p>

      <ul className={styles.kinds}>
        {DATA_KINDS.map((kind) => {
          const own = uploads.files.filter((file) => file.kind === kind.id);
          const count =
            kind.countKey && layers ? (layers.properties.counts[kind.countKey] ?? 0) : 0;
          // Площадки, загруженные слоем KML, лежат в своём файле, а не в параметрах.
          const ownLayer = kind.id === 'site' && byKind.has('sites');
          const source = byKind.get(ownLayer ? 'sites' : kind.source);
          const shared = kind.source === 'params' && kind.id !== 'params' && !ownLayer;
          return (
            <li key={kind.id} className={styles.kind}>
              <div className={styles.head}>
                {kind.layerGroup !== null ? (
                  <Toggle
                    // Пустой слой показывать нечего: переключатель выключен и недоступен.
                    checked={count > 0 && filter.groups.includes(kind.layerGroup)}
                    onChange={(shown) =>
                      props.onFilterChange(toggleGroup(filter, kind.layerGroup ?? '', shown))
                    }
                    disabled={count === 0}
                    title={`Показывать на карте: ${kind.label.toLowerCase()}`}
                  />
                ) : (
                  <span className={styles.noLayer} title="На карте не рисуется" />
                )}
                <span className={styles.text}>
                  <span className={styles.label}>{kind.label}</span>
                  <span className={styles.count}>{describeCount(kind, layers)}</span>
                </span>
                <UploadButton kind={kind} onPick={uploads.add} />
              </div>

              {(source || own.length > 0) && (
                <ul className={styles.files}>
                  {/* Файл слоя, все объекты которого вне области, тоже виден: иначе
                      не видно, что импорт его прочитал и отбросил. */}
                  {source &&
                    sourceId &&
                    (count > 0 || kind.countKey === null || (source.stats?.read ?? 0) > 0) && (
                      <SourceFile scenarioId={sourceId} file={source} shared={shared} />
                    )}
                  {own.map((file) => (
                    <li key={file.id} className={styles.file}>
                      <span className={styles.fileName} title={file.name}>
                        {file.name}
                      </span>
                      <span className={styles.fileSize}>{fileSize(file.size)}</span>
                      <span
                        className={styles.stub}
                        title="Будет прочитан по кнопке «Собрать сценарий»"
                      >
                        ждёт сборки
                      </span>
                      <button
                        type="button"
                        className={styles.remove}
                        onClick={() => uploads.remove(file.id)}
                        title="Убрать из списка"
                        aria-label={`Убрать ${file.name}`}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

      {warnings.length > 0 && (
        <details className={styles.warnings}>
          <summary>Замечания импорта ({warnings.length})</summary>
          <ul>
            {warnings.map((warning, index) => (
              <li key={`${warning.code}-${index}`}>{warning.message}</li>
            ))}
          </ul>
        </details>
      )}

      {uploads.error && <Notice tone="error">{uploads.error}</Notice>}
      {uploads.imported && uploads.imported.id === props.scenarioId && (
        <Notice tone="success">
          Сценарий «{uploads.imported.name}» собран из файлов: площадь{' '}
          {decimal(uploads.imported.report.area_km2 ?? 0, 1)} км². Итог импорта каждого слоя — под
          видами выше.
        </Notice>
      )}

      {pending > 0 ? (
        <div className={styles.assemble}>
          <button
            type="button"
            className={styles.assembleButton}
            onClick={() => void uploads.assemble()}
            disabled={uploads.busy}
          >
            {uploads.busy
              ? 'Сборка…'
              : uploads.files.some((file) => file.kind === 'scenario')
                ? 'Загрузить сценарий'
                : 'Собрать копию с заменой'}
          </button>
          <p className={styles.help}>
            Выбрано файлов: {pending}.{' '}
            {uploads.files.some((file) => file.kind === 'scenario')
              ? 'Сценарий будет проверен при загрузке; ошибка — с указанием поля.'
              : `Слои KML будут прочитаны и проверены, по каждому — итог импорта. ${borrowed ?? ''}`}
          </p>
        </div>
      ) : (
        <p className={styles.help}>
          Кнопки «Загрузить» заменяют слой выбранного сценария — например, только препятствия:
          остальное возьмётся из него, сохранится копия. Сценарий целиком (JSON) загружается как
          есть. Новый сценарий с нуля — «Новый сценарий из своих файлов» в панели слева.
        </p>
      )}
    </div>
  );
}
