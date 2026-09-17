import { exportUrl } from '../../api';
import styles from './result.module.css';

type Props = { jobId: string };

export function ExportLinks({ jobId }: Props) {
  return (
    <p className={styles.exports}>
      Выгрузить: <a href={exportUrl(jobId, 'kml')}>KML</a> ·{' '}
      <a href={exportUrl(jobId, 'geojson')}>GeoJSON</a>
    </p>
  );
}
