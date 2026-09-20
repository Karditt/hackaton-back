/**
 * Транспорт: базовый адрес и разбор ошибок.
 *
 * Префикс `/api` снимает прокси Vite (vite.config.ts), поэтому фронт и API
 * живут на одном origin и вопроса CORS в разработке нет.
 */

export const API_BASE = '/api';

/** Ошибка контракта от FastAPI: место и объяснение. */
type FieldError = { loc?: (string | number)[]; msg?: string };

/** Служебная приставка pydantic перед сообщением валидатора — человеку не нужна. */
const PYDANTIC_PREFIX = /^Value error,\s*/;

/**
 * Причина отказа для показа человеку.
 *
 * FastAPI кладёт в `detail` либо строку (её пишет ядро: самопересечение с
 * местом, слишком большая область, неизвестный борт), либо список ошибок
 * контракта с местом. Показывается и то, и другое: пользователь правит
 * параметры сценария и должен видеть, какое поле не принято (заказчик, №19).
 */
function describeFailure(status: number, statusText: string, body: string): string {
  let detail: unknown;
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail;
  } catch {
    detail = body;
  }
  if (typeof detail === 'string' && detail !== '') {
    return `${status}: ${detail}`;
  }
  if (Array.isArray(detail)) {
    const reasons = (detail as FieldError[])
      .map((item) => {
        const place = (item.loc ?? []).filter((part) => part !== 'body').join('.');
        const reason = String(item.msg).replace(PYDANTIC_PREFIX, '');
        return place ? `${place}: ${reason}` : reason;
      })
      .join('; ');
    return `${status}: ${reasons}`;
  }
  return `${status} ${statusText}: ${body}`;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    throw new Error(describeFailure(response.status, response.statusText, await response.text()));
  }
  return (await response.json()) as T;
}
