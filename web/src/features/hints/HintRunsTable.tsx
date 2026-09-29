import type { HintReport, HintRun } from '../../api';
import { composition } from '../../shared/fleet';
import { hours } from '../../shared/format';
import styles from './hints.module.css';

type Props = {
  report: HintReport;
  /** Имя модели по идентификатору. */
  modelName: (vehicleId: string) => string;
};

/** Итог прогона: выполним, не успевает к сроку или невыполним. */
function verdict(run: HintRun): { label: string; tone: string | undefined } {
  if (run.makespan_s == null) {
    return { label: 'невыполним', tone: styles.bad };
  }
  if (run.meets_deadline === false) {
    return { label: 'не успевает', tone: styles.late };
  }
  return { label: 'выполним', tone: styles.good };
}

/**
 * Прогоны подсказки по возрастанию N. Строка ответа выделена. Под числами —
 * состав, первая причина невыполнимости и как подобран состав, если не по
 * порядку пула: так таблица помещается в боковую панель.
 */
export function HintRunsTable({ report, modelName }: Props) {
  const runs = report.runs ?? [];
  if (runs.length === 0) return null;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>N</th>
            <th>Итог</th>
            <th>Срок</th>
            <th>Ресурс</th>
            <th title="Вылетов">Выл.</th>
          </tr>
        </thead>
        {runs.map((run) => {
          const { label, tone } = verdict(run);
          const reason = run.makespan_s == null ? run.reasons?.[0]?.message : undefined;
          return (
            <tbody
              key={run.boards}
              className={run.boards === report.answer_boards ? styles.answerRow : undefined}
              title={`Прогон конвейера — ${Math.round(run.elapsed_s)} с`}
            >
              <tr>
                <td>
                  <b>{run.boards}</b>
                </td>
                <td className={tone}>{label}</td>
                <td>{run.makespan_s == null ? '—' : hours(run.makespan_s)}</td>
                <td>{run.resource_s == null ? '—' : hours(run.resource_s)}</td>
                <td>{run.sortie_count ?? '—'}</td>
              </tr>
              <tr className={styles.detailRow}>
                <td />
                <td colSpan={4}>
                  <span className={styles.fleet}>{composition(run.fleet, modelName)}</span>
                  {reason && <span className={styles.reason}>{reason}</span>}
                  {run.note && <span>Состав: {run.note}</span>}
                </td>
              </tr>
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
