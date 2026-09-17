import { request } from './http';
import type { ScenarioSummary } from './types';

export async function listScenarios(): Promise<ScenarioSummary[]> {
  const payload = await request<{ scenarios: ScenarioSummary[] }>('/scenarios');
  return payload.scenarios;
}
