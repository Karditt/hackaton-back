/**
 * Подбор места ещё одной площадки: постановка, поллинг, пробы (E15).
 *
 * Первая проба — расчёт «как есть» без приписки бортов к площадкам, дальше —
 * по кандидату у скоплений дальней работы. Каждая проба — план с краш-тестом,
 * обычная задача расчёта `plan_job_id`; на её карте видна предложенная
 * площадка. Здесь ничего не считается.
 */

import { request } from './http';
import type { SitesStatus } from './types';

/** @param scenarioId идентификатор сохранённого сценария */
export async function startSites(scenarioId: string): Promise<SitesStatus> {
  return request<SitesStatus>(`/scenarios/${encodeURIComponent(scenarioId)}/sites`, {
    method: 'POST',
  });
}

export async function getSites(searchId: string): Promise<SitesStatus> {
  return request<SitesStatus>(`/sites/${encodeURIComponent(searchId)}`);
}

/**
 * Дождаться подбора поллингом: пробы приходят по мере расчёта.
 *
 * @param searchId идентификатор задачи подбора
 * @param onTick вызывается на каждом опросе — для прогресса и готовых проб
 * @param intervalMs период опроса, миллисекунды
 */
export async function waitForSites(
  searchId: string,
  onTick: (status: SitesStatus) => void,
  intervalMs = 1000,
): Promise<SitesStatus> {
  for (;;) {
    const status = await getSites(searchId);
    onTick(status);
    if (status.state === 'done' || status.state === 'failed') {
      return status;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
