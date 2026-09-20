/**
 * Справочник техники — `GET /vehicles`. Нужен панели параметров, чтобы
 * состав флота набирался из каталога, а не вписывался строкой.
 *
 * Пробелы ТТХ (`gaps`) и выведенные поля (`derived_fields`) приходят вместе с
 * бортом намеренно: интерфейс обязан показывать, какое число взято из
 * документа, а какое выведено допущением (инвариант 6).
 */

import { request } from './http';
import type { Vehicle } from './types';

export async function listVehicles(): Promise<Vehicle[]> {
  const payload = await request<{ vehicles?: Vehicle[] }>('/vehicles');
  return payload.vehicles ?? [];
}
