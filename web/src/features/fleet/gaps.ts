/**
 * Пробелы каталога для показа.
 *
 * Пробел приходит с сервера строкой «cruise_speed_mps: текст»: по имени поля
 * карточка модели понимает, к какому числу он относится. Человеку вместо
 * имени поля нужна подпись.
 */

/** Поля ТТХ и оптики словами. */
const FIELD_TITLES: Record<string, string> = {
  cruise_speed_mps: 'Крейсерская скорость',
  max_speed_mps: 'Максимальная скорость',
  min_speed_mps: 'Минимальная скорость',
  endurance_s: 'Продолжительность полёта',
  max_wind_mps: 'Допустимый ветер',
  turn_radius_m: 'Радиус разворота',
  min_altitude_agl_m: 'Минимальная безопасная высота',
  max_altitude_agl_m: 'Потолок над землёй',
  max_range_m: 'Протяжённость маршрута',
  energy_wh: 'Энергия АКБ',
  battery: 'АКБ',
  max_payload_mass_kg: 'Масса полезной нагрузки',
  survey_area_per_sortie: 'Площадь съёмки за вылет',
  focal_length_mm: 'Фокусное расстояние',
  sensor_width_mm: 'Ширина матрицы',
  sensor_height_mm: 'Высота матрицы',
  image_width_px: 'Ширина кадра',
  image_height_px: 'Высота кадра',
};

/** Имя поля словами; незнакомое — как есть. */
export function fieldTitle(key: string): string {
  return FIELD_TITLES[key] ?? key;
}

/** Пробел без имени поля впереди — для подсказки у самого числа. */
export function gapBody(gap: string): string {
  const match = /^([a-z_]+):\s*/.exec(gap);
  return match ? gap.slice(match[0].length) : gap;
}

/**
 * Пробел целиком для списка: «Радиус разворота: текст». Если текст и так
 * начинается с названия поля, повтора нет.
 */
export function gapText(gap: string): string {
  const match = /^([a-z_]+):\s*/.exec(gap);
  if (!match) return gap;
  const title = fieldTitle(match[1]);
  const body = gap.slice(match[0].length);
  return body.toLowerCase().startsWith(title.toLowerCase()) ? body : `${title}: ${body}`;
}
