/**
 * Форматирование чисел для показа. Только перевод единиц и округление —
 * сами величины приходят с сервера.
 */

/** @param seconds длительность, секунды */
export function minutes(seconds: number): string {
  return `${(seconds / 60).toFixed(1)} мин`;
}

/** @param seconds длительность, секунды — часы и минуты для длинных величин */
export function hours(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
}

/**
 * Время суток UTC для момента «начало работ + смещение».
 *
 * @param originMs начало работ, миллисекунды эпохи
 * @param offsetSeconds смещение от начала работ, секунды
 */
export function clockTime(originMs: number, offsetSeconds: number): string {
  const moment = new Date(originMs + offsetSeconds * 1000);
  const day = moment.toISOString().slice(5, 10);
  const time = moment.toISOString().slice(11, 16);
  return `${day} ${time}`;
}

/** @param metres расстояние, метры */
export function kilometres(metres: number): string {
  return `${(metres / 1000).toFixed(1)} км`;
}

/** Число с русской десятичной запятой, не больше `digits` знаков после неё. */
export function decimal(value: number, digits = 1): string {
  return value.toLocaleString('ru-RU', { maximumFractionDigits: digits });
}

/**
 * Существительное в нужной форме после числа: 1 зона, 3 зоны, 5 зон.
 *
 * @param forms формы для 1, 2–4 и 5–20: `['зона', 'зоны', 'зон']`
 */
export function plural(count: number, forms: [one: string, few: string, many: string]): string {
  const tens = Math.abs(count) % 100;
  const units = tens % 10;
  if (tens >= 11 && tens <= 14) return forms[2];
  if (units === 1) return forms[0];
  if (units >= 2 && units <= 4) return forms[1];
  return forms[2];
}

/** @param bytes размер файла, байты */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${decimal(bytes / 1024, 0)} КБ`;
  return `${decimal(bytes / (1024 * 1024), 1)} МБ`;
}

/** Экранировать строку для вставки в HTML (подписи Leaflet собираются строкой). */
export function escapeHtml(value: unknown): string {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
