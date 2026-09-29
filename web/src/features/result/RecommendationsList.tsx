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

/** Итог проверки совета расчётом: хватает ли правки; без проверки — ничего. */
function CheckBadge({ checked }: { checked?: boolean | null }) {
  if (checked == null) return null;
  return checked ? (
    <span className={styles.checkedOk}> · проверено: хватает</span>
  ) : (
    <span className={styles.checkedShort}> · проверено: одного этого мало</span>
  );
}

/**
 * Что добавить, чтобы задание стало выполнимым (заказчик, №33, №42). Советы
 * «площадка», «борт», «вылеты» сервер проверяет расчётом сценария с правкой —
 * итог в конце сообщения и отметкой.
 */
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
          <CheckBadge checked={item.checked} />
          <br />
          {item.message}
        </li>
      ))}
    </ul>
  );
}
