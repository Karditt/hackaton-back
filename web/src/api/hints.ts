/**
 * Подсказки по пулу техники: постановка вопроса, поллинг, отчёт (E6, E10).
 *
 * Флот сценария — пул. На каждое число бортов сервер прогоняет обычный
 * конвейер с краш-тестом, поэтому ответ подтверждён планом; этот план — обычная
 * задача расчёта `plan_job_id` со всеми выгрузками `/jobs/{id}/…`. Слой
 * представления здесь ничего не считает: вопрос уходит серверу, ответ
 * показывается как есть.
 */

import { request } from './http';
import type { HintStatus } from './types';

/**
 * Вопрос подсказки. Один из трёх: `boards` — срок при N бортах, `sweep` — срок
 * при каждом N, только `deadlineS` — сколько бортов нужно к сроку. Срок можно
 * добавить к `boards` и `sweep`: тогда ответ скажет, успевают ли.
 */
export type HintQuestion = {
  /** Число бортов N, от 1. */
  boards?: number;
  /** Срок от начала работ, секунды. */
  deadlineS?: number;
  /** Срок при каждом N — от одного борта до всего пула. */
  sweep?: boolean;
};

/**
 * Поставить вопрос-подсказку по сценарию.
 *
 * @param scenarioId идентификатор сохранённого сценария; его флот — пул
 * @param question вопрос: число бортов, срок или перебор
 */
export async function startHint(scenarioId: string, question: HintQuestion): Promise<HintStatus> {
  const query = new URLSearchParams();
  if (question.boards !== undefined) query.set('boards', String(question.boards));
  if (question.deadlineS !== undefined) query.set('deadline_s', String(question.deadlineS));
  if (question.sweep) query.set('sweep', 'true');
  return request<HintStatus>(`/scenarios/${encodeURIComponent(scenarioId)}/hints?${query}`, {
    method: 'POST',
  });
}

export async function getHint(hintId: string): Promise<HintStatus> {
  return request<HintStatus>(`/hints/${encodeURIComponent(hintId)}`);
}

/**
 * Дождаться ответа поллингом — как `waitForJob`. Вопрос идёт несколькими
 * прогонами конвейера, на данных заказчика — минуты, поэтому опрос реже.
 *
 * @param hintId идентификатор задачи подсказки
 * @param onTick вызывается на каждом опросе — для прогресса по прогонам
 * @param intervalMs период опроса, миллисекунды
 */
export async function waitForHint(
  hintId: string,
  onTick: (status: HintStatus) => void,
  intervalMs = 1000,
): Promise<HintStatus> {
  for (;;) {
    const status = await getHint(hintId);
    onTick(status);
    if (status.state === 'done' || status.state === 'failed') {
      return status;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
