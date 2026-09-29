import type { GeoJsonDocument, Recommendation, RecommendationKind } from '../../api';
import styles from './result.module.css';

const KIND_LABEL: Record<RecommendationKind, string> = {
  add_site: 'Площадка',
  add_vehicle: 'Борт',
  add_batteries: 'Вылеты',
  other_model: 'Другая модель',
  wait: 'Подождать',
  relax_requirement: 'Требование',
};

type Props = {
  items: Recommendation[];
  /** Выгрузка плана: место совета «площадка» в WGS84 — признак `recommended_site`. */
  document?: GeoJsonDocument;
  /** Добавить площадку в сценарий; нет — кнопки нет. */
  onAddSite?: (lon: number, lat: number) => void;
  busy?: boolean;
};

/** Место совета в WGS84: признак выгрузки с тем же текстом совета. */
function adviceSpot(document: GeoJsonDocument | undefined, message: string) {
  const feature = document?.features.find(
    (item) =>
      item.properties.kind === 'recommended_site' &&
      item.properties.message === message &&
      item.geometry.type === 'Point',
  );
  const coordinates = feature?.geometry.coordinates as [number, number] | undefined;
  return coordinates ? { lon: coordinates[0], lat: coordinates[1] } : null;
}

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
 * итог в конце сообщения и отметкой. Проверенный совет «площадка» можно
 * принять одной кнопкой: площадка встаёт в копию сценария.
 */
export function RecommendationsList({ items, document, onAddSite, busy }: Props) {
  if (items.length === 0) {
    return <p className={styles.empty}>Рекомендаций нет.</p>;
  }
  return (
    <ul className={styles.warnings}>
      {items.map((item, index) => {
        const spot =
          item.kind === 'add_site' && item.checked === true && onAddSite
            ? adviceSpot(document, item.message)
            : null;
        return (
          <li key={`${item.kind}-${index}`} className={`${styles.warningItem} ${styles.advice}`}>
            <b>{KIND_LABEL[item.kind] ?? item.kind}</b>
            {item.position ? ' · место отмечено на карте' : ''}
            <CheckBadge checked={item.checked} />
            <br />
            {item.message}
            {spot && onAddSite && (
              <>
                <br />
                <button
                  type="button"
                  className={styles.linkButton}
                  onClick={() => onAddSite(spot.lon, spot.lat)}
                  disabled={busy}
                >
                  Добавить эту площадку в сценарий →
                </button>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
