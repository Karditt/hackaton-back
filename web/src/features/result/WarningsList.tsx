import type { PlanWarning, Severity, Stage } from '../../api';
import styles from './result.module.css';

const SEVERITY_LABEL: Record<Severity, string> = {
  error: 'Ошибка',
  warning: 'Внимание',
  info: 'Справка',
};

const SEVERITY_CLASS: Record<Severity, string | undefined> = {
  error: styles.severityError,
  warning: styles.severityWarning,
  info: styles.severityInfo,
};

/** Этап расчёта, к которому относится сообщение, — словами, а не кодом. */
const STAGE_LABEL: Record<Stage, string> = {
  geo: 'исходные данные',
  survey: 'параметры съёмки',
  decompose: 'разбиение области',
  coverage: 'маршруты съёмки',
  flight: 'модель полёта',
  transit: 'перелёты',
  allocate: 'распределение работ',
  schedule: 'расписание',
  validate: 'проверка плана',
  advise: 'рекомендации',
  export: 'выгрузка',
};

/** Значение по умолчанию в контракте, если поле не пришло. */
const DEFAULT_SEVERITY: Severity = 'warning';

type Props = { warnings: PlanWarning[] };

export function WarningsList({ warnings }: Props) {
  if (warnings.length === 0) {
    return <p className={styles.empty}>Нет.</p>;
  }
  return (
    <ul className={styles.warnings}>
      {warnings.map((warning, index) => {
        const severity = warning.severity ?? DEFAULT_SEVERITY;
        return (
          <li
            key={`${warning.code}-${index}`}
            className={`${styles.warningItem} ${SEVERITY_CLASS[severity]}`}
          >
            <b>{SEVERITY_LABEL[severity]}</b>
            <span className={styles.stage}> · {STAGE_LABEL[warning.stage] ?? warning.stage}</span>
            <br />
            {warning.message}
          </li>
        );
      })}
    </ul>
  );
}
