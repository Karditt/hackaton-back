import { request } from './http';
import type { RejectedScenario, ScenarioSummary } from './types';

/** Сценарии хранилища и файлы, которые не прочитались по контракту (долг №28). */
export type ScenarioListing = { scenarios: ScenarioSummary[]; rejected: RejectedScenario[] };

export async function listScenarios(): Promise<ScenarioListing> {
  const payload = await request<Partial<ScenarioListing>>('/scenarios');
  return { scenarios: payload.scenarios ?? [], rejected: payload.rejected ?? [] };
}
