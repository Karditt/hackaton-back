import type { ReactNode } from 'react';
import styles from './Notice.module.css';

type Props = {
  tone: 'error' | 'caution';
  children: ReactNode;
};

/** Плашка с сообщением: ошибка расчёта или предупреждение о заглушках. */
export function Notice({ tone, children }: Props) {
  return <p className={`${styles.notice} ${styles[tone]}`}>{children}</p>;
}
