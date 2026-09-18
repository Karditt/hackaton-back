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

/** Экранировать строку для вставки в HTML (подписи Leaflet собираются строкой). */
export function escapeHtml(value: unknown): string {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
