import { useEffect, useState } from 'react';
import { listVehicles, type Vehicle } from '../../api';

/**
 * Каталог техники, загружается один раз при открытии страницы. Состав флота
 * набирается из него: выдуманных бортов в сценарии быть не может (инвариант 6).
 */
export function useVehicles() {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listVehicles()
      .then((loaded) => {
        if (!cancelled) setVehicles(loaded);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(`Каталог техники недоступен: ${cause.message}`);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { vehicles, error };
}
