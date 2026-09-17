import { useEffect, useRef } from 'react';
import type L from 'leaflet';
import type { GeoJsonDocument } from '../../api';
import { createMap } from './baseMap';
import { drawMission } from './missionLayer';
import styles from './MapView.module.css';

type Props = {
  /** План для отрисовки; `null` — карта без маршрутов. */
  mission: GeoJsonDocument | null;
};

/** Карта Leaflet. Живёт вне React-дерева, React только подаёт ей данные. */
export function MapView({ mission }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);

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
    if (!map || !mission) return;
    const layer = drawMission(map, mission);
    return () => {
      layer.remove();
    };
  }, [mission]);

  return <div className={styles.map} ref={containerRef} />;
}
