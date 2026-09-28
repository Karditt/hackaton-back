import { useCallback, useState } from 'react';
import {
  fetchScenarioFile,
  importScenario,
  saveScenario,
  type ImportedScenario,
  type Scenario,
} from '../../api';
import type { DataKindId } from './dataKinds';

/** Файл, выбранный кнопкой «Загрузить». */
export type UploadedFile = {
  id: string;
  kind: DataKindId;
  name: string;
  /** Размер, байты. */
  size: number;
  file: File;
};

/** Суффикс сценария, собранного из загруженных файлов поверх выбранного. */
const UPLOAD_SUFFIX = '--upload';

/** Суффиксы производных сценариев веба: правка параметров и сборка из файлов. */
const DERIVED_SUFFIXES = ['--custom', UPLOAD_SUFFIX];

/** Поля геометрии сценария — их приносят слои KML, а не параметры задания. */
const GEOMETRY_KEYS = ['area', 'no_fly_zones', 'obstacles'] as const;

/** Виды данных, которые живут в параметрах задания (JSON). */
const PARAMS_KINDS: DataKindId[] = ['params', 'site', 'airspace'];

type Options = {
  /** Выбранный сценарий — что добирается, если файла вида не загружено. */
  scenarioId: string;
  scenario: Scenario | null;
  /** Сценарий, чьи исходные файлы есть на сервере; null — файлов нет. */
  sourceId: string | null;
  /** Сценарий сохранён — выбрать его. */
  onCreated: (scenarioId: string) => void;
};

/** Идентификатор без суффиксов производных сценариев. */
function baseId(scenarioId: string): string {
  return DERIVED_SUFFIXES.reduce(
    (id, suffix) => id.replace(new RegExp(`${suffix}$`), ''),
    scenarioId,
  );
}

/** Последний загруженный файл вида. */
function latest(files: UploadedFile[], kinds: DataKindId[]): UploadedFile | undefined {
  return [...files].reverse().find((file) => kinds.includes(file.kind));
}

/** Разобрать JSON-файл в объект; ошибка называет файл. */
async function readObject(file: File): Promise<Record<string, unknown>> {
  let value: unknown;
  try {
    value = JSON.parse(await file.text());
  } catch (cause) {
    throw new Error(`Файл ${file.name} — не JSON: ${(cause as Error).message}`);
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`Файл ${file.name}: ожидается объект JSON`);
  }
  return value as Record<string, unknown>;
}

/**
 * Загрузка исходных данных файлами (E10, долг №49).
 *
 * Файлы копятся по видам, затем «Собрать сценарий» отправляет их серверу:
 * сценарий целиком — в `POST /scenarios`, слои KML с параметрами задания — в
 * `POST /scenarios/import`, где их читает ядро и отвечает отчётом импорта.
 * Вид, файла которого не загрузили, берётся у выбранного сценария: его
 * исходный файл с сервера, а параметры — из самого сценария без геометрии.
 * Так можно заменить один слой — например, только препятствия. Разбора
 * геометрии здесь нет: KML уходит на сервер как есть.
 */
export function useUploads({ scenarioId, scenario, sourceId, onCreated }: Options) {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imported, setImported] = useState<ImportedScenario | null>(null);

  const add = useCallback((kind: DataKindId, picked: FileList | null) => {
    if (!picked || picked.length === 0) return;
    const added = Array.from(picked, (file, index) => ({
      id: `${Date.now()}-${index}-${file.name}`,
      kind,
      name: file.name,
      size: file.size,
      file,
    }));
    setFiles((current) => [...current, ...added]);
    setError(null);
  }, []);

  const remove = useCallback((id: string) => {
    setFiles((current) => current.filter((file) => file.id !== id));
  }, []);

  /** Параметры задания JSON-текстом и идентификатор нового сценария. */
  const paramsText = useCallback(async (): Promise<string> => {
    const own = latest(files, PARAMS_KINDS);
    let params: Record<string, unknown>;
    if (own) {
      params = await readObject(own.file);
    } else {
      const source = sourceId ? await fetchScenarioFile(sourceId, 'params') : null;
      if (source) {
        params = JSON.parse(await source.text()) as Record<string, unknown>;
      } else if (scenario) {
        params = { ...scenario } as Record<string, unknown>;
        for (const key of GEOMETRY_KEYS) delete params[key];
      } else {
        throw new Error('Нет параметров задания: загрузите JSON параметров или выберите сценарий');
      }
      const base = baseId(scenarioId || String(params.id ?? 'scenario'));
      params.id = `${base}${UPLOAD_SUFFIX}`;
      params.name = `${String(params.name || base)} (загруженные файлы)`;
    }
    return JSON.stringify(params);
  }, [files, scenario, scenarioId, sourceId]);

  /** Слой: загруженный файл или исходный файл выбранного сценария. */
  const layer = useCallback(
    async (kind: 'area' | 'zones' | 'obstacles', id: DataKindId) => {
      const own = latest(files, [id]);
      if (own) return { file: own.file as Blob, name: own.name };
      const source = sourceId ? await fetchScenarioFile(sourceId, kind) : null;
      return source ? { file: source, name: `${kind}.kml` } : null;
    },
    [files, sourceId],
  );

  const assemble = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const whole = latest(files, ['scenario']);
      if (whole) {
        // Форму проверит сервер: неполный сценарий — 422 с местом и причиной.
        const saved = await saveScenario((await readObject(whole.file)) as unknown as Scenario);
        setImported(null);
        setFiles([]);
        onCreated(saved.id);
        return;
      }
      const area = await layer('area', 'area');
      if (!area) {
        throw new Error('Нужна область съёмки: загрузите KML области');
      }
      const result = await importScenario({
        area: area.file,
        areaName: area.name,
        zones: await layer('zones', 'no_fly_zone'),
        obstacles: await layer('obstacles', 'obstacle'),
        params: await paramsText(),
      });
      setImported(result);
      setFiles([]);
      onCreated(result.id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [files, layer, onCreated, paramsText]);

  return { files, add, remove, assemble, busy, error, imported };
}

/** Состояние и действия загрузки — то, что получает менеджер данных. */
export type Uploads = ReturnType<typeof useUploads>;
