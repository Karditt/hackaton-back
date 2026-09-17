/**
 * Расчёт: постановка задачи, поллинг статуса, получение результата.
 *
 * Слой представления НЕ считает маршруты, длины и время — он показывает то,
 * что вернул сервер (AGENT.md §2). Любая формула, появившаяся в `web/`,
 * написана не в том слое.
 */

import { API_BASE, request } from './http';
import type { GeoJsonDocument, JobStatus, PipelineResult } from './types';

export type ExportFormat = 'geojson' | 'kml';

export async function startPlanning(scenarioId: string, alpha: number): Promise<JobStatus> {
  const query = new URLSearchParams({ alpha: String(alpha) });
  return request<JobStatus>(`/scenarios/${encodeURIComponent(scenarioId)}/plan?${query}`, {
    method: 'POST',
  });
}

export async function getJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`/jobs/${jobId}`);
}

/** Полный результат прогона: все стадии конвейера, а не только итог. */
export async function getResult(jobId: string): Promise<PipelineResult> {
  return request<PipelineResult>(`/jobs/${jobId}/result`);
}

/** Выгрузка плана в GeoJSON — её же рисует карта. */
export async function fetchMissionGeoJson(jobId: string): Promise<GeoJsonDocument> {
  return request<GeoJsonDocument>(`/jobs/${jobId}/export?format=geojson`);
}

/** Ссылка на выгрузку. Скачивание — задача браузера, не приложения. */
export function exportUrl(jobId: string, format: ExportFormat): string {
  return `${API_BASE}/jobs/${jobId}/export?format=${format}`;
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
