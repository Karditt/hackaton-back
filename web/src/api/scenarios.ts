import { API_BASE, request } from './http';
import type {
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
