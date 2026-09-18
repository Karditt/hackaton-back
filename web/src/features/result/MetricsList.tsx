import type { PipelineResult } from '../../api';
import { hours, kilometres, minutes } from '../../shared/format';
import styles from './result.module.css';

type Props = { result: PipelineResult };

const MODE_LABEL: Record<string, string> = {
  min_makespan: 'минимум срока',
  min_resource: 'минимум ресурса',
};

/** Метрики выполнимого плана (PROJECT_BRIEF.md §4.3). */
export function MetricsList({ result }: Props) {
  const { mission, crs } = result;
  const metrics = mission.metrics;
  if (!metrics) return null;

  const rows: [label: string, value: string][] = [
    ['Режим', MODE_LABEL[metrics.mode] ?? metrics.mode],
    ['Алгоритм', mission.algorithm],
    ['Срок (makespan)', hours(metrics.makespan_s)],
    ['Ресурс: налёт + циклы', hours(metrics.resource_s)],
    ['Суммарный налёт', hours(metrics.total_flight_time_s)],
    ['Циклов взлёт-посадка', String(metrics.cycle_count)],
    ['Вылетов / бортов', `${metrics.sortie_count} / ${metrics.vehicle_count}`],
    ['Подобластей снято', `${metrics.cells_covered} из ${metrics.cells_planned}`],
    ['Дистанция', kilometres(metrics.total_distance_m)],
    ['Проекция', `EPSG:${crs.epsg}`],
  ];
  if (metrics.deadline_s != null) {
    rows.push([
      'Ограничение по сроку',
      `${minutes(metrics.deadline_s)} — ${metrics.deadline_met ? 'укладываемся' : 'не укладываемся'}`,
    ]);
  }

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
