import { API_BASE, request } from './http';
import type {
  ImportedScenario,
  RejectedScenario,
  Scenario,
  ScenarioFile,
  ScenarioFiles,
  ScenarioLayers,
  ScenarioSummary,
} from './types';

/** Сценарии хранилища и файлы, которые не прочитались по контракту (долг №28). */
export type ScenarioListing = { scenarios: ScenarioSummary[]; rejected: RejectedScenario[] };

export async function listScenarios(): Promise<ScenarioListing> {
  const payload = await request<Partial<ScenarioListing>>('/scenarios');
  return { scenarios: payload.scenarios ?? [], rejected: payload.rejected ?? [] };
}

/** Сценарий целиком — источник значений панели параметров (E10). */
export async function getScenario(scenarioId: string): Promise<Scenario> {
  return request<Scenario>(`/scenarios/${encodeURIComponent(scenarioId)}`);
}

/**
 * Слои сценария в GeoJSON — исходные данные на карте до расчёта (E10). Их
 * собирает ядро: фронт не переводит сценарий в признаки сам.
 */
export async function getScenarioLayers(scenarioId: string): Promise<ScenarioLayers> {
  return request<ScenarioLayers>(`/scenarios/${encodeURIComponent(scenarioId)}/layers`);
}

/**
 * Исходные файлы сценария по видам и что с ними сделал импорт (E10): слои KML
 * в формате заказчика, параметры задания, собранный JSON.
 */
export async function getScenarioFiles(scenarioId: string): Promise<ScenarioFiles> {
  return request<ScenarioFiles>(`/scenarios/${encodeURIComponent(scenarioId)}/files`);
}

/** Ссылка на исходный файл сценария: скачивание — задача браузера. */
export function scenarioFileUrl(scenarioId: string, kind: ScenarioFile['kind']): string {
  return `${API_BASE}/scenarios/${encodeURIComponent(scenarioId)}/files/${kind}`;
}

/**
 * Исходный файл сценария содержимым — чтобы собрать новый сценарий, заменив в
 * нём один слой (E10). null — такого файла у сценария нет.
 */
export async function fetchScenarioFile(
  scenarioId: string,
  kind: ScenarioFile['kind'],
): Promise<Blob | null> {
  const response = await fetch(scenarioFileUrl(scenarioId, kind));
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`Файл ${kind} сценария «${scenarioId}» не получен: ${response.status}`);
  }
  return response.blob();
}

/** Файл слоя для сборки сценария: содержимое и имя, под которым он уйдёт на сервер. */
export type LayerFile = { file: Blob; name: string };

/** Файлы для сборки сценария: слои KML и параметры задания JSON-текстом. */
export type ImportFiles = {
  area: Blob;
  areaName: string;
  zones: LayerFile | null;
  obstacles: LayerFile | null;
  /** Площадки точками KML; null — из параметров, а нет и там — подберёт сервис. */
  sites?: LayerFile | null;
  params: string;
};

/**
 * Собрать сценарий из слоёв KML и параметров задания — `POST /scenarios/import`
 * (E9, E10). Читает слои ядро (`core/geo/kml_import.py`); ответ — сохранённый
 * сценарий и отчёт импорта по слоям. Отказ — 422 с причиной.
 */
export async function importScenario(files: ImportFiles): Promise<ImportedScenario> {
  const form = new FormData();
  form.append('params', files.params);
  form.append('area', files.area, files.areaName);
  if (files.zones) form.append('zones', files.zones.file, files.zones.name);
  if (files.obstacles) form.append('obstacles', files.obstacles.file, files.obstacles.name);
  if (files.sites) form.append('sites', files.sites.file, files.sites.name);
  return request<ImportedScenario>('/scenarios/import', { method: 'POST', body: form });
}

/**
 * Сохранить сценарий. Одноимённый перезаписывается, поэтому правки панели
 * параметров ложатся в один производный сценарий, а не плодят файлы.
 *
 * Содержание проверяет сервер (`core.pipeline.check_scenario`): недопустимое
 * значение — 422 с причиной, а не тихая починка (заказчик, №19).
 */
export async function saveScenario(scenario: Scenario): Promise<ScenarioSummary> {
  return request<ScenarioSummary>('/scenarios', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scenario),
  });
}
