import type { Recommendation, RecommendationKind } from '../../api';
import styles from './result.module.css';

const KIND_LABEL: Record<RecommendationKind, string> = {
  add_site: 'Площадка',
  add_vehicle: 'Борт',
  add_batteries: 'Вылеты',
  other_model: 'Другая модель',
  wait: 'Подождать',
  relax_requirement: 'Требование',
};

type Props = { items: Recommendation[] };

/** Что добавить, чтобы задание стало выполнимым (заказчик, №33, №42). */
export function RecommendationsList({ items }: Props) {
  if (items.length === 0) {
    return <p className={styles.empty}>Рекомендаций нет.</p>;
  }
  return (
    <ul className={styles.warnings}>
      {items.map((item, index) => (
        <li key={`${item.kind}-${index}`} className={`${styles.warningItem} ${styles.advice}`}>
          <b>{KIND_LABEL[item.kind] ?? item.kind}</b>
          {item.position ? ' · место отмечено на карте' : ''}
          <br />
          {item.message}
        </li>
      ))}
    </ul>
  );
}
