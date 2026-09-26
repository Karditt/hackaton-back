/**
 * Высота обзора над землёй в углу карты (E11).
 *
 * Карта Leaflet плоская, камеры и её высоты у неё нет. Показывается условная
 * высота: с неё камера с вертикальным углом обзора `VIEW_ANGLE_DEG` видела бы
 * по высоте окна ту же полосу местности, что сейчас видна на карте. Величина
 * знакома по трёхмерным глобусам: приближаешь карту — высота падает.
 *
 * Длина видимой полосы берётся у самой карты (`map.distance` — расстояние по
 * эллипсоиду между серединами верхнего и нижнего края), поэтому зависимость
 * масштаба Меркатора от широты учтена без собственных формул.
 *
 * Это свойство вида, а не плана: к высотам полёта (над землёй, AGL) в плане
 * отношения не имеет, поэтому число не уходит ни в расчёт, ни в выгрузку.
 */

import L from 'leaflet';
import { decimal } from '../../shared/format';

/**
 * Вертикальный угол обзора условной камеры, градусы. Соглашение показа, а не
 * физика плана: при 60° высота равна 0,87 видимой полосы.
 */
const VIEW_ANGLE_DEG = 60;

/** События карты, после которых видимая полоса меняется. */
const EVENTS = 'zoomend moveend resize';

/**
 * Высота условной камеры над землёй, метры.
 *
 * @param visibleM длина видимой полосы местности по высоте окна, метры
 */
export function viewAltitudeM(visibleM: number): number {
  const halfAngle = (VIEW_ANGLE_DEG * Math.PI) / 180 / 2;
  return visibleM / 2 / Math.tan(halfAngle);
}

/** Подпись высоты: до километра — в метрах, дальше — в километрах. */
export function formatAltitude(metres: number): string {
  if (metres < 1000) return `${Math.round(metres / 10) * 10} м`;
  if (metres < 100_000) return `${decimal(metres / 1000, 1)} км`;
  return `${decimal(metres / 1000, 0)} км`;
}

/** Длина полосы местности по высоте окна карты, метры. */
function visibleHeightM(map: L.Map): number {
  const size = map.getSize();
  const top = map.containerPointToLatLng([size.x / 2, 0]);
  const bottom = map.containerPointToLatLng([size.x / 2, size.y]);
  return map.distance(top, bottom);
}

/** Элемент управления в углу карты: «высота обзора над землёй ≈ 12,3 км». */
export function viewAltitudeControl(position: L.ControlPosition = 'bottomleft'): L.Control {
  const control = new L.Control({ position });
  let value: HTMLElement | null = null;

  const update = function (this: L.Map) {
    if (!value) return;
    const size = this.getSize();
    // Спрятанная карта (другая вкладка) имеет нулевой размер — показывать нечего.
    value.textContent =
      size.y > 0 ? `≈ ${formatAltitude(viewAltitudeM(visibleHeightM(this)))}` : '—';
  };

  control.onAdd = (map) => {
    const container = L.DomUtil.create('div', 'geoscan-altitude');
    container.title =
      'Высота, с которой при угле обзора 60° видна та же полоса местности, что на экране. ' +
      'Меняется с масштабом. Это не высота полёта бортов';
    L.DomUtil.create('div', 'geoscan-altitude__label', container).textContent = 'высота над землёй';
    value = L.DomUtil.create('div', 'geoscan-altitude__value', container);
    L.DomEvent.disableClickPropagation(container);
    map.on(EVENTS, update, map);
    update.call(map);
    return container;
  };

  control.onRemove = (map) => {
    map.off(EVENTS, update, map);
    value = null;
  };

  return control;
}
