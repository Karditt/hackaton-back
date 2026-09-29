import { request } from './http';

/**
 * Парк техники на сервере (долг №50): вкладка «Техника» видит один и тот же
 * парк на любом компьютере. Форма парка — `features/fleet/park.ts`; сервер её
 * не толкует, только хранит.
 */
export async function fetchPark(): Promise<unknown | null> {
  const payload = await request<{ park: unknown | null }>('/park');
  return payload.park;
}

/** Сохранить парк на сервере. */
export async function storePark(park: unknown): Promise<void> {
  await request<unknown>('/park', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(park),
  });
}
