import { exportUrl } from '../../api';
import styles from './result.module.css';

type Props = { jobId: string; feasible: boolean };

/** Ссылки на выгрузку. У невыполнимого плана — отчёт, расписания нет. */
export function ExportLinks({ jobId, feasible }: Props) {
  return (
    <p className={styles.exports}>
      {feasible ? 'Выгрузить план: ' : 'Выгрузить отчёт: '}
      <a href={exportUrl(jobId, 'kml')}>KML</a> ·{' '}
      <a href={exportUrl(jobId, 'geojson')}>GeoJSON</a>
      {feasible && (
        <>
          {' '}
          · <a href={exportUrl(jobId, 'schedule')}>расписание CSV</a>
        </>
      )}
    </p>
  );
}
