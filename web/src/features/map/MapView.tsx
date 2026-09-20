import { useEffect, useMemo, useRef } from 'react';
import type L from 'leaflet';
import type { GeoJsonDocument } from '../../api';
import { createMap } from './baseMap';
import { filterMission, type MapFilter } from './layers';
import { drawMission } from './missionLayer';
import styles from './MapView.module.css';

type Props = {
  /** План для отрисовки; `null` — карта без маршрутов. */
  mission: GeoJsonDocument | null;
  /** Какие слои и чей маршрут показывать (E11). */
  filter: MapFilter;
};

/** Карта Leaflet. Живёт вне React-дерева, React только подаёт ей данные. */
export function MapView({ mission, filter }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  // Какой план уже показан целиком: масштаб подбирается один раз на план,
  // а не на каждую галочку слоя.
  const fittedRef = useRef<GeoJsonDocument | null>(null);

  const shown = useMemo(
    () => (mission ? filterMission(mission, filter) : null),
    [mission, filter],
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
    const map = mapRef.current;
    if (!map || !mission || !shown) return;
    const fit = fittedRef.current !== mission;
    const layer = drawMission(map, shown, fit);
    fittedRef.current = mission;
    return () => {
      layer.remove();
    };
  }, [mission, shown]);

  return <div className={styles.map} ref={containerRef} />;
}
