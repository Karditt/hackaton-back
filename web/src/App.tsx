/**
 * Каркас интерфейса (E10/E11).
 *
 * Сейчас это вертикальный срез из Фазы 1 архитектуры: выбрать сценарий →
 * запустить расчёт → увидеть маршруты на карте → выгрузить KML. Редактор
 * сценария (рисование области, зон и площадок мышью) — это E10, его здесь нет.
 *
 * Слой представления ничего не пересчитывает: все числа приходят с сервера.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import {
  exportUrl,
  fetchMissionGeoJson,
  listScenarios,
  startPlanning,
  waitForJob,
  type PipelineResult,
  type PlanWarning,
  type ScenarioSummary,
} from './api';
import { createMap, drawMission } from './map';

const SEVERITY_LABEL: Record<string, string> = {
  error: 'ОШИБКА',
  warning: 'ВНИМАНИЕ',
  info: 'справка',
};

function minutes(seconds: number): string {
  return `${(seconds / 60).toFixed(1)} мин`;
}

function kilometres(metres: number): string {
  return `${(metres / 1000).toFixed(1)} км`;
}

export default function App() {
  const mapContainer = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.GeoJSON | null>(null);

  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioId, setScenarioId] = useState<string>('');
  const [alpha, setAlpha] = useState(0.5);
  const [status, setStatus] = useState<string>('готов');
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mapContainer.current && !mapRef.current) {
      mapRef.current = createMap(mapContainer.current);
    }
  }, []);

  useEffect(() => {
    listScenarios()
      .then((items) => {
        setScenarios(items);
        if (items.length > 0) setScenarioId(items[0].id);
      })
      .catch((cause: Error) => setError(`API недоступен: ${cause.message}`));
  }, []);

  const plan = useCallback(async () => {
    if (!scenarioId) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setStatus('расчёт поставлен в очередь');
    try {
      const job = await startPlanning(scenarioId, alpha);
      setJobId(job.id);
      const finished = await waitForJob(job.id, (tick) => setStatus(`расчёт: ${tick.state}`));
      if (finished.state === 'failed') {
        throw new Error(finished.error ?? 'расчёт не удался');
      }
      const document = await fetchMissionGeoJson(job.id);
      const { getResult } = await import('./api');
      setResult(await getResult(job.id));

      if (mapRef.current) {
        if (layerRef.current) mapRef.current.removeLayer(layerRef.current);
        layerRef.current = drawMission(mapRef.current, document);
      }
      setStatus('готово');
    } catch (cause) {
      setError((cause as Error).message);
      setStatus('ошибка');
    } finally {
      setBusy(false);
    }
  }, [scenarioId, alpha]);

  const warnings: PlanWarning[] = result
    ? [
        ...result.survey_plan.warnings,
        ...result.coverage.warnings,
        ...result.allocation.warnings,
        ...result.mission.warnings,
      ]
    : [];

  return (
    <div className="layout">
      <aside className="panel">
        <h1>GEOSCAN</h1>
        <p className="subtitle">Планирование беспилотных авиационных работ</p>

        <label>
          Сценарий
          <select
            value={scenarioId}
            onChange={(event) => setScenarioId(event.target.value)}
            disabled={busy || scenarios.length === 0}
          >
            {scenarios.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name || item.id}
              </option>
            ))}
          </select>
        </label>

        <label>
          Критерий оптимальности: α = {alpha.toFixed(2)}
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={alpha}
            onChange={(event) => setAlpha(Number(event.target.value))}
            disabled={busy}
          />
          <span className="hint">
            0 — минимум суммарного налёта, 1 — минимум времени выполнения работ
          </span>
        </label>

        <button onClick={plan} disabled={busy || !scenarioId}>
          {busy ? 'Считаю…' : 'Рассчитать'}
        </button>

        <p className="status">Состояние: {status}</p>
        {error && <p className="error">{error}</p>}

        {result && (
          <>
            <h2>Метрики</h2>
            <dl className="metrics">
              <dt>Алгоритм</dt>
              <dd>{result.mission.algorithm}</dd>
              <dt>Время работ (makespan)</dt>
              <dd>{minutes(result.mission.metrics.makespan_s)}</dd>
              <dt>Суммарный налёт</dt>
              <dd>{minutes(result.mission.metrics.total_flight_time_s)}</dd>
              <dt>Дистанция</dt>
              <dd>{kilometres(result.mission.metrics.total_distance_m)}</dd>
              <dt>Вылетов / бортов</dt>
              <dd>
                {result.mission.metrics.sortie_count} / {result.mission.metrics.vehicle_count}
              </dd>
              <dt>Галсов покрыто</dt>
              <dd>
                {result.mission.metrics.strips_covered} из {result.mission.metrics.strips_planned}
              </dd>
              <dt>Проекция</dt>
              <dd>EPSG:{result.crs.epsg}</dd>
            </dl>

            {result.mission.stub_stages.length > 0 && (
              <p className="stub">
                План рассчитан с заглушками на стадиях: {result.mission.stub_stages.join(', ')}.
                Это не окончательный расчёт.
              </p>
            )}

            <h2>Предупреждения ({warnings.length})</h2>
            <ul className="warnings">
              {warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`} className={warning.severity}>
                  <b>{SEVERITY_LABEL[warning.severity] ?? warning.severity}</b> · {warning.stage}
                  <br />
                  {warning.message}
                </li>
              ))}
            </ul>

            {jobId && (
              <p className="exports">
                Выгрузить: <a href={exportUrl(jobId, 'kml')}>KML</a> ·{' '}
                <a href={exportUrl(jobId, 'geojson')}>GeoJSON</a>
              </p>
            )}
          </>
        )}
      </aside>

      <main className="map" ref={mapContainer} />
    </div>
  );
}
