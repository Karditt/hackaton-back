import type { ReactNode } from 'react';
import styles from './Toggle.module.css';

type Props = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Подпись рядом с переключателем; без неё обязателен `title`. */
  children?: ReactNode;
  /** Подсказка и доступное имя, когда подписи нет. */
  title?: string;
  disabled?: boolean;
};

/**
 * Переключатель «включено / выключено»: показ слоя на карте, борт в строю.
 * Это обычный флажок с оформлением — клавиатура и чтение с экрана работают
 * без дополнительного кода.
 */
export function Toggle({ checked, onChange, children, title, disabled }: Props) {
  return (
    <label className={styles.toggle} title={title}>
      <input
        type="checkbox"
        className={styles.input}
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        disabled={disabled}
        aria-label={children ? undefined : title}
      />
      <span className={styles.track} aria-hidden="true" />
      {children && <span className={styles.label}>{children}</span>}
    </label>
  );
}
