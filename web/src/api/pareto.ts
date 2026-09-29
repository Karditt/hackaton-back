/**
 * Фронт «срок — ресурс»: постановка, поллинг, точки (E12).
 *
 * Края фронта — планы «минимум срока» и «минимум ресурса», между ними —
 * «минимум ресурса» к промежуточным срокам. Каждая точка — план с
 * краш-тестом; он лежит обычной задачей расчёта `plan_job_id` со всеми
 * выгрузками `/jobs/{id}/…`. Здесь ничего не считается.
 */

import { request } from './http';
import type { ParetoStatus } from './types';

/**
 * Поставить фронт в расчёт.
 *
 * @param scenarioId идентификатор сохранённого сценария; его флот — пул
 * @param steps сколько промежуточных сроков между краями; без него — по умолчанию сервера
 */
export async function startPareto(scenarioId: string, steps?: number): Promise<ParetoStatus> {
  const query = new URLSearchParams();
  if (steps !== undefined) query.set('steps', String(steps));
  return request<ParetoStatus>(`/scenarios/${encodeURIComponent(scenarioId)}/pareto?${query}`, {
    method: 'POST',
  });
}

export async function getPareto(frontId: string): Promise<ParetoStatus> {
  return request<ParetoStatus>(`/pareto/${encodeURIComponent(frontId)}`);
}

/**
 * Дождаться фронта поллингом — как `waitForHint`: точки приходят по мере
 * расчёта, и каждый опрос отдаёт готовые.
 *
 * @param frontId идентификатор задачи фронта
 * @param onTick вызывается на каждом опросе — для прогресса и готовых точек
 * @param intervalMs период опроса, миллисекунды
 */
export async function waitForPareto(
  frontId: string,
  onTick: (status: ParetoStatus) => void,
  intervalMs = 1000,
): Promise<ParetoStatus> {
  for (;;) {
    const status = await getPareto(frontId);
    onTick(status);
    if (status.state === 'done' || status.state === 'failed') {
      return status;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
