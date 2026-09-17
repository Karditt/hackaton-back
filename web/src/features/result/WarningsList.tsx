import type { PlanWarning, Severity } from '../../api';
import styles from './result.module.css';

const SEVERITY_LABEL: Record<Severity, string> = {
  error: 'ОШИБКА',
  warning: 'ВНИМАНИЕ',
  info: 'справка',
};

const SEVERITY_CLASS: Record<Severity, string | undefined> = {
  error: styles.severityError,
  warning: styles.severityWarning,
  info: styles.severityInfo,
};

/** Значение по умолчанию в core/schemas.py, если поле не пришло. */
const DEFAULT_SEVERITY: Severity = 'warning';

type Props = { warnings: PlanWarning[] };

export function WarningsList({ warnings }: Props) {
  return (
    <ul className={styles.warnings}>
      {warnings.map((warning, index) => {
        const severity = warning.severity ?? DEFAULT_SEVERITY;
        return (
          <li key={`${warning.code}-${index}`} className={`${styles.warningItem} ${SEVERITY_CLASS[severity]}`}>
            <b>{SEVERITY_LABEL[severity]}</b> · {warning.stage}
            <br />
            {warning.message}
          </li>
        );
      })}
    </ul>
  );
}
