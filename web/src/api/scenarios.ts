import { request } from './http';
import type { RejectedScenario, Scenario, ScenarioSummary } from './types';

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
