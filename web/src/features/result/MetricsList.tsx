import type { PipelineResult } from '../../api';
import { kilometres, minutes } from '../../shared/format';
import styles from './result.module.css';

type Props = { result: PipelineResult };

export function MetricsList({ result }: Props) {
  const { mission, crs } = result;
  const { metrics } = mission;
  const rows: [label: string, value: string][] = [
    ['Алгоритм', mission.algorithm],
    ['Время работ (makespan)', minutes(metrics.makespan_s)],
    ['Суммарный налёт', minutes(metrics.total_flight_time_s)],
    ['Дистанция', kilometres(metrics.total_distance_m)],
    ['Вылетов / бортов', `${metrics.sortie_count} / ${metrics.vehicle_count}`],
    ['Галсов покрыто', `${metrics.strips_covered} из ${metrics.strips_planned}`],
    ['Проекция', `EPSG:${crs.epsg}`],
  ];

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
