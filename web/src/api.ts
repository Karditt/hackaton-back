/**
 * Клиент API.
 *
 * Слой представления НЕ считает маршруты, длины и время — он показывает то,
 * что вернул сервер (AGENT.md §2). Любая формула, появившаяся в `web/`,
 * написана не в том слое.
 *
 * Типы здесь минимальные и ручные — ровно те поля, которые нужны каркасу.
 * Полные типы порождаются из контракта:
 *   python -m core.cli export-schema -o web/src/types/schema.json
 *   npm run gen:types
 * После этого руками писать формы контракта нельзя (AGENT.md §2).
 */

const BASE = '/api';

export type ScenarioSummary = { id: string; name: string };

export type JobStatus = {
  id: string;
  scenario_id: string;
  state: 'pending' | 'running' | 'done' | 'failed';
  progress: number;
  error: string | null;
  mission_id: string | null;
};

/** Предупреждение конвейера. Заглушки стадий сообщают о себе именно так. */
export type PlanWarning = {
  code: string;
  severity: 'info' | 'warning' | 'error';
  stage: string;
  message: string;
};

export type MissionMetrics = {
  makespan_s: number;
  total_flight_time_s: number;
  total_distance_m: number;
  sortie_count: number;
  vehicle_count: number;
  strips_planned: number;
  strips_covered: number;
  objective_value: number;
  alpha: number;
};

/** Полный результат прогона: все стадии конвейера, а не только итог. */
export type PipelineResult = {
  scenario_id: string;
  crs: { epsg: number; name: string };
  coverage: { strips: unknown[]; azimuth_deg: number; warnings: PlanWarning[] };
  allocation: { algorithm: string; warnings: PlanWarning[] };
  mission: {
    id: string;
    algorithm: string;
    alpha: number;
    metrics: MissionMetrics;
    warnings: PlanWarning[];
    stub_stages: string[];
    sorties: unknown[];
  };
  survey_plan: { warnings: PlanWarning[]; reference_vehicle_id: string };
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return (await response.json()) as T;
}

export async function listScenarios(): Promise<ScenarioSummary[]> {
  const payload = await request<{ scenarios: ScenarioSummary[] }>('/scenarios');
  return payload.scenarios;
}

export async function getScenario(id: string): Promise<Record<string, unknown>> {
  return request(`/scenarios/${id}`);
}

export async function startPlanning(id: string, alpha: number): Promise<JobStatus> {
  return request<JobStatus>(`/scenarios/${id}/plan?alpha=${alpha}`, { method: 'POST' });
}

export async function getJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`/jobs/${jobId}`);
}

export async function getResult(jobId: string): Promise<PipelineResult> {
  return request<PipelineResult>(`/jobs/${jobId}/result`);
}

/** Ссылка на выгрузку. Скачивание — задача браузера, не приложения. */
export function exportUrl(jobId: string, format: 'geojson' | 'kml'): string {
  return `${BASE}/jobs/${jobId}/export?format=${format}`;
}

/**
 * Дождаться завершения расчёта поллингом.
 *
 * Поллинг, а не WebSocket: так решено брифом (§3). WS — только если
 * останется время после функциональности, дающей баллы.
 *
 * @param jobId идентификатор задачи
 * @param onTick вызывается на каждом опросе — для индикатора прогресса
 * @param intervalMs период опроса, миллисекунды
 */
export async function waitForJob(
  jobId: string,
  onTick: (status: JobStatus) => void,
  intervalMs = 400,
): Promise<JobStatus> {
  for (;;) {
    const status = await getJob(jobId);
    onTick(status);
    if (status.state === 'done' || status.state === 'failed') {
      return status;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

/** Выгрузка плана в GeoJSON — её же рисует карта. */
export async function fetchMissionGeoJson(jobId: string): Promise<GeoJsonDocument> {
  return request<GeoJsonDocument>(`/jobs/${jobId}/export?format=geojson`);
}

export type GeoJsonFeature = {
  type: 'Feature';
  geometry: { type: string; coordinates: unknown };
  properties: Record<string, unknown>;
};

export type GeoJsonDocument = {
  type: 'FeatureCollection';
  features: GeoJsonFeature[];
  properties?: Record<string, unknown>;
};
