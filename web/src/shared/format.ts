/**
 * Форматирование чисел для показа. Только перевод единиц и округление —
 * сами величины приходят с сервера.
 */

/** @param seconds длительность, секунды */
export function minutes(seconds: number): string {
  return `${(seconds / 60).toFixed(1)} мин`;
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
