/**
 * Транспорт: базовый адрес и разбор ошибок.
 *
 * Префикс `/api` снимает прокси Vite (vite.config.ts), поэтому фронт и API
 * живут на одном origin и вопроса CORS в разработке нет.
 */

export const API_BASE = '/api';

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return (await response.json()) as T;
}
