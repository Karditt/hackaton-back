import type { ReactNode } from 'react';
import styles from './Section.module.css';

type Props = {
  title: ReactNode;
  children: ReactNode;
};

/** Раздел боковой панели с заголовком. */
export function Section({ title, children }: Props) {
  return (
    <section className={styles.section}>
      <h2 className={styles.title}>{title}</h2>
      {children}
    </section>
  );
}
