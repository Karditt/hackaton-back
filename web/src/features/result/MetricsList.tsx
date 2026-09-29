import type { PipelineResult, Vehicle } from '../../api';
import { boardCount, composition } from '../../shared/fleet';
import { hours, kilometres, minutes } from '../../shared/format';
import styles from './result.module.css';

type Props = {
  result: PipelineResult;
  /** Каталог моделей — для имён в составе плана. */
  vehicles: Vehicle[];
};

export const MODE_LABEL: Record<string, string> = {
  min_makespan: 'минимум срока',
  min_resource: 'минимум ресурса',
};

/**
 * Алгоритм распределения словами. Значение приходит строкой вида
 * `constructive:earliest_free+local_search`; неизвестное показывается как есть.
 */
export function algorithmLabel(algorithm: string): string {
  if (algorithm.startsWith('baseline')) return 'равномерное деление (для сравнения)';
  if (algorithm.includes('local_search')) return 'эвристика с локальным поиском';
  if (algorithm.startsWith('constructive')) return 'конструктивная эвристика';
  return algorithm;
}

/** Метрики выполнимого плана (PROJECT_BRIEF.md §4.3). */
export function MetricsList({ result, vehicles }: Props) {
  const { mission, crs } = result;
  const metrics = mission.metrics;
  if (!metrics) return null;

  const modelName = (vehicleId: string) =>
    vehicles.find((vehicle) => vehicle.id === vehicleId)?.name ?? vehicleId;
  const used = mission.fleet_used ?? [];
  const idle = mission.fleet_idle ?? [];

  const rows: [label: string, value: string][] = [
    ['Срок выполнения работ', hours(metrics.makespan_s)],
    ['Ресурс техники: налёт + циклы', hours(metrics.resource_s)],
    ['Суммарный налёт', hours(metrics.total_flight_time_s)],
    ['Циклов взлёт-посадка', String(metrics.cycle_count)],
    ['Вылетов / бортов', `${metrics.sortie_count} / ${metrics.vehicle_count}`],
  ];
  if (used.length > 0) {
    // Состав плана выбирает расчёт: флот сценария — пул доступной техники.
    const pool = boardCount(used) + boardCount(idle);
    rows.push([
      'Состав плана',
      `${composition(used, modelName)} — ${boardCount(used)} из ${pool} бортов пула`,
    ]);
    if (idle.length > 0) rows.push(['На земле', composition(idle, modelName)]);
  }
  rows.push(
    ['Подобластей снято', `${metrics.cells_covered} из ${metrics.cells_planned}`],
    ['Протяжённость маршрутов', kilometres(metrics.total_distance_m)],
  );
  if (metrics.deadline_s != null) {
    rows.push([
      'Ограничение по сроку',
      `${minutes(metrics.deadline_s)} — ${metrics.deadline_met ? 'укладываемся' : 'не укладываемся'}`,
    ]);
  }
  rows.push(
    ['Критерий', MODE_LABEL[metrics.mode] ?? metrics.mode],
    ['Алгоритм', algorithmLabel(mission.algorithm)],
    ['Система координат расчёта', `${crs.name || 'UTM'} (EPSG:${crs.epsg})`],
  );

  return (
    <dl className={styles.metrics}>
      {rows.map(([label, value]) => (
        <div key={label} className={styles.metricRow}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
