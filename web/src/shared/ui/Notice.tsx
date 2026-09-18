import type { ReactNode } from 'react';
import styles from './Notice.module.css';

type Props = {
  tone: 'error' | 'caution' | 'success';
  children: ReactNode;
};

/** Плашка с сообщением: итог расчёта, ошибка или предупреждение о заглушках. */
export function Notice({ tone, children }: Props) {
  return <p className={`${styles.notice} ${styles[tone]}`}>{children}</p>;
}
