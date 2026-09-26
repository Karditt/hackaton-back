import { useEffect, useMemo, useRef } from 'react';
import type L from 'leaflet';
import type { GeoJsonDocument } from '../../api';
import { createMap } from './baseMap';
import { filterMission, type MapFilter } from './layers';
import { drawMission } from './missionLayer';
import styles from './MapView.module.css';

type Props = {
  /**
   * Что рисовать: план (или отчёт о невыполнимости), а до расчёта — слои
   * сценария. `null` — пустая карта.
   */
  document: GeoJsonDocument | null;
  /** Какие слои и чей маршрут показывать (E11). */
  filter: MapFilter;
  /**
   * Видна ли карта. Спрятанная вкладка имеет нулевой размер, и Leaflet надо
   * сказать, что окно снова появилось, — иначе тайлы подгрузятся не все.
   */
  visible: boolean;
};

/** Карта Leaflet. Живёт вне React-дерева, React только подаёт ей данные. */
export function MapView({ document, filter, visible }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  // Какие документы уже показаны целиком: масштаб подбирается один раз на
  // документ, а не на каждую галочку слоя. Слои сценария, вернувшиеся на
  // карту после правки параметров, масштаб тоже не сбрасывают.
  const fittedRef = useRef(new WeakSet<GeoJsonDocument>());

  const shown = useMemo(
    () => (document ? filterMission(document, filter) : null),
    [document, filter],
  );

  useEffect(() => {
    if (!containerRef.current) return;
    const map = createMap(containerRef.current);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (visible) mapRef.current?.invalidateSize();
  }, [visible]);

  // Окно карты меняется не только со сменой вкладки: границу панели двигают
  // мышью. Leaflet о новом размере не знает, пока ему не сказать.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => mapRef.current?.invalidateSize({ pan: false }));
    });
    observer.observe(container);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !document || !shown) return;
    // У спрятанной карты нулевой размер, и подбор масштаба дал бы весь мир.
    // Такой документ подгоняется, когда карту покажут снова.
    const fit = visible && !fittedRef.current.has(document);
    const layer = drawMission(map, shown, fit);
    if (fit) fittedRef.current.add(document);
    return () => {
      layer.remove();
    };
  }, [document, shown, visible]);

  return <div className={styles.map} ref={containerRef} />;
}
