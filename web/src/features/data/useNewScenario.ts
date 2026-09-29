import { useCallback, useState } from 'react';
import { importScenario, type ImportedScenario, type Scenario } from '../../api';
import { readObject } from './useUploads';

/** Файлы нового сценария по видам: слои KML и параметры задания JSON. */
export type NewScenarioSlot = 'area' | 'zones' | 'obstacles' | 'sites' | 'params';

/** Поля сценария, которые у образца не берутся: геометрия другого района и его имя. */
const NOT_FROM_TEMPLATE = [
  'id',
  'name',
  'description',
  'area',
  'no_fly_zones',
  'obstacles',
  'sites',
  'airspace_boundary',
] as const;

/** Идентификатор нового сценария — по моменту создания: «user-20260929-153012». */
function newScenarioId(): string {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  return `user-${stamp}`;
}

/**
 * Параметры задания по образцу выбранного сценария: тип съёмки, GSD, флот,
 * коридор, ветер, сроки, ТТХ пользователя. Геометрии, площадок и приписки
 * бортов к площадкам нет — они другого района.
 */
function templateParams(template: Scenario): Record<string, unknown> {
  const params = { ...template } as Record<string, unknown>;
  for (const key of NOT_FROM_TEMPLATE) delete params[key];
  params.fleet = template.fleet.map((unit) => ({ ...unit, site_id: null }));
  return params;
}

type Options = {
  /** Сценарий-образец параметров задания — выбранный сейчас; null — ещё не загружен. */
  template: Scenario | null;
  /** Сценарий создан — выбрать его. */
  onCreated: (scenarioId: string) => void;
};

/**
 * Новый сценарий из своих файлов (E10): область — обязательно, зоны,
 * препятствия, площадки и параметры — по желанию.
 *
 * Всё уходит в `POST /scenarios/import`, слои читает ядро и отвечает отчётом
 * импорта. Параметров нет — берутся у выбранного сценария без его геометрии и
 * площадок; площадок нет ни файлом, ни в параметрах — места подберёт сервис.
 * В отличие от замены слоя в менеджере данных, у выбранного сценария не
 * берётся ни один слой: новый сценарий — новый район.
 */
export function useNewScenario({ template, onCreated }: Options) {
  const [name, setName] = useState('');
  const [files, setFiles] = useState<Partial<Record<NewScenarioSlot, File>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ImportedScenario | null>(null);

  const pick = useCallback((slot: NewScenarioSlot, file: File | null) => {
    setFiles((current) => {
      const next = { ...current };
      if (file) next[slot] = file;
      else delete next[slot];
      return next;
    });
    setError(null);
  }, []);

  const reset = useCallback(() => {
    setName('');
    setFiles({});
    setError(null);
  }, []);

  const create = useCallback(async (): Promise<boolean> => {
    const area = files.area;
    if (!area) {
      setError('Нужна область съёмки: выберите файл KML области');
      return false;
    }
    setBusy(true);
    setError(null);
    try {
      let params: Record<string, unknown>;
      if (files.params) {
        params = await readObject(files.params);
      } else if (template) {
        params = templateParams(template);
      } else {
        throw new Error(
          'Нет параметров задания: выберите файл JSON или дождитесь загрузки сценария',
        );
      }
      params.id = newScenarioId();
      params.name = name.trim() || String(params.name || '') || 'Новый сценарий';
      const layer = (file: File | undefined) => (file ? { file, name: file.name } : null);
      const result = await importScenario({
        area,
        areaName: area.name,
        zones: layer(files.zones),
        obstacles: layer(files.obstacles),
        sites: layer(files.sites),
        params: JSON.stringify(params),
      });
      setCreated(result);
      reset();
      onCreated(result.id);
      return true;
    } catch (cause) {
      setError((cause as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }, [files, name, onCreated, reset, template]);

  return { name, setName, files, pick, reset, create, busy, error, created };
}

/** Состояние и действия формы нового сценария. */
export type NewScenario = ReturnType<typeof useNewScenario>;
