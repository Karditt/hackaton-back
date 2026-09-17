import styles from './App.module.css';

export function AppHeader() {
  return (
    <header>
      <h1 className={styles.title}>GEOSCAN</h1>
      <p className={styles.subtitle}>Планирование беспилотных авиационных работ</p>
    </header>
  );
}
