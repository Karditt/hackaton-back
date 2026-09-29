import type { FleetUnit } from '../api';

/**
 * Состав флота одной строкой: «3 × Геоскан 201, 2 × Геоскан Gemini». Строки
 * одной модели с разными площадками складываются: здесь важно, сколько бортов
 * какой модели, а площадки видны в плане.
 */
export function composition(fleet: FleetUnit[], modelName: (vehicleId: string) => string) {
  const counts = new Map<string, number>();
  for (const unit of fleet) {
    counts.set(unit.vehicle_id, (counts.get(unit.vehicle_id) ?? 0) + (unit.count ?? 1));
  }
  return [...counts].map(([vehicleId, count]) => `${count} × ${modelName(vehicleId)}`).join(', ');
}

/** Сколько бортов в строках состава. */
export function boardCount(fleet: FleetUnit[]): number {
  return fleet.reduce((sum, unit) => sum + (unit.count ?? 1), 0);
}
