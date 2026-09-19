import { exportUrl, packageUrl, type BoardSummary } from '../../api';
import styles from './result.module.css';

type Props = { jobId: string; feasible: boolean; boards: BoardSummary[] };

/**
 * Ссылки на выгрузку (docs/export_format.md). Выполнимый план — обзор, архив
 * всего комплекта и индивидуальное задание каждого борта; невыполнимый — отчёт.
 */
export function ExportLinks({ jobId, feasible, boards }: Props) {
  return (
    <>
      <p className={styles.exports}>
        {feasible ? 'Выгрузить план: ' : 'Выгрузить отчёт: '}
        <a href={exportUrl(jobId, 'kml')}>KML</a> ·{' '}
        <a href={exportUrl(jobId, 'geojson')}>GeoJSON</a>
        {feasible && (
          <>
            {' '}
            · <a href={exportUrl(jobId, 'schedule')}>расписание CSV</a> ·{' '}
            <a href={packageUrl(jobId)}>всё архивом ZIP</a>
          </>
        )}
      </p>
      {feasible && boards.length > 0 && (
        <p className={styles.boardLinks}>
          Задания бортов, KML:{' '}
          {boards.map((board, index) => (
            <span key={board.board_id}>
              {index > 0 && ' · '}
              <a href={exportUrl(jobId, 'kml', board.board_id)}>{board.label}</a>
            </span>
          ))}
        </p>
      )}
    </>
  );
}
