import { exportUrl } from '../../api';
import { clockTime, hours } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import type { PlanningRun } from '../planning';
import {
  ExportLinks,
  RecommendationsList,
  ScheduleGantt,
  ScheduleTable,
  WarningsList,
} from '../result';
import styles from './SchedulePage.module.css';

type Props = {
  /** Текущий расчёт; null — плана нет или параметры изменены после расчёта. */
  run: PlanningRun | null;
  /** Идёт расчёт. */
  busy: boolean;
  /** Вернуться к вкладке «План». */
  onGoPlan: () => void;
  /** Показать на карте только этот борт. */
  onShowBoard: (boardId: string) => void;
};

const MODE_LABEL: Record<string, string> = {
  min_makespan: 'минимум срока',
  min_resource: 'минимум ресурса',
};

/** Плитка сводки: крупное число и подпись. */
function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.tile}>
      <span className={styles.tileValue}>{value}</span>
      <span className={styles.tileLabel}>{label}</span>
    </div>
  );
}

/**
 * Вкладка «Расписание» (E11, заказчик №31): диаграмма по бортам во всю ширину,
 * сводка по бортам и все вылеты таблицей.
 *
 * В боковой панели карты на полторы сотни вылетов диаграмма не читается,
 * поэтому у расписания своя вкладка. Всё, что здесь показано, посчитано на
 * сервере; страница только раскладывает.
 */
export function SchedulePage({ run, busy, onGoPlan, onShowBoard }: Props) {
  if (run === null) {
    return (
      <div className={styles.page}>
        <div className={styles.empty}>
          <h1 className={styles.title}>Расписания пока нет</h1>
          <p>
            {busy
              ? 'Идёт расчёт — расписание появится, когда он закончится.'
              : 'Расписание — часть рассчитанного плана. Выберите сценарий и нажмите «Рассчитать» на вкладке «План». Если параметры правились после расчёта, план нужно пересчитать.'}
          </p>
          <button type="button" className={styles.primary} onClick={onGoPlan}>
            Перейти к плану
          </button>
        </div>
      </div>
    );
  }

  const { result } = run;
  const mission = result.mission;
  const boards = run.document.properties?.boards ?? [];

  if (mission.status !== 'feasible' || !mission.schedule) {
    return (
      <div className={styles.page}>
        <h1 className={styles.title}>Расписания нет — задание невыполнимо</h1>
        <Notice tone="error">
          Частичный план не выдаётся, поэтому и расписания нет. Ниже — причины и что добавить.
        </Notice>
        <section className={styles.section}>
          <h2 className={styles.heading}>Причины</h2>
          <WarningsList warnings={mission.reasons ?? []} />
        </section>
        <section className={styles.section}>
          <h2 className={styles.heading}>Что добавить</h2>
          <RecommendationsList items={mission.recommendations ?? []} />
        </section>
        <ExportLinks jobId={run.jobId} feasible={false} boards={[]} />
      </div>
    );
  }

  const schedule = mission.schedule;
  const metrics = mission.metrics;
  const origin = schedule.start_time ? Date.parse(schedule.start_time) : null;
  const moment = (seconds: number) =>
    origin === null ? `T+${hours(seconds)}` : clockTime(origin, seconds);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Расписание вылетов</h1>
          <p className={styles.subtitle}>
            Сценарий <b>{run.scenarioId}</b> · {MODE_LABEL[run.mode] ?? run.mode} ·{' '}
            {origin === null
              ? 'время относительное, от начала работ'
              : `начало работ ${clockTime(origin, 0)} UTC`}
          </p>
        </div>
        <a className={styles.download} href={exportUrl(run.jobId, 'schedule')}>
          Скачать CSV
        </a>
      </header>

      {metrics && (
        <div className={styles.tiles}>
          <Tile label="срок (makespan)" value={hours(metrics.makespan_s)} />
          <Tile label="вылетов" value={String(metrics.sortie_count)} />
          <Tile label="бортов" value={String(metrics.vehicle_count)} />
          <Tile label="суммарный налёт" value={hours(metrics.total_flight_time_s)} />
          <Tile label="ресурс: налёт + циклы" value={hours(metrics.resource_s)} />
        </div>
      )}

      <section className={styles.section}>
        <h2 className={styles.heading}>Диаграмма по бортам</h2>
        <ScheduleGantt schedule={schedule} boards={boards} variant="wide" />
      </section>

      <section className={styles.section}>
        <h2 className={styles.heading}>Борта</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Борт</th>
                <th>Модель</th>
                <th>Вылетов</th>
                <th>В воздухе</th>
                <th>Первый взлёт</th>
                <th>Последняя посадка</th>
                <th aria-label="Действия" />
              </tr>
            </thead>
            <tbody>
              {boards.map((board) => (
                <tr key={board.board_id}>
                  <td>{board.label}</td>
                  <td className={styles.muted}>{board.vehicle_id}</td>
                  <td>{board.sortie_count}</td>
                  <td>{hours(board.flight_time_s)}</td>
                  <td>{moment(board.first_takeoff_s)}</td>
                  <td>{moment(board.last_landing_s)}</td>
                  <td className={styles.actions}>
                    <button
                      type="button"
                      className={styles.link}
                      onClick={() => onShowBoard(board.board_id)}
                    >
                      на карте
                    </button>
                    <a href={exportUrl(run.jobId, 'kml', board.board_id)}>задание KML</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={styles.heading}>Все вылеты ({schedule.entries?.length ?? 0})</h2>
        <ScheduleTable schedule={schedule} boards={boards} detailed />
      </section>
    </div>
  );
}
