/**
 * Расчёт: постановка задачи, поллинг статуса, получение результата.
 *
 * Слой представления НЕ считает маршруты, длины и время — он показывает то,
 * что вернул сервер (docs/dev/AGENT.md §2). Любая формула, появившаяся в `web/`,
 * написана не в том слое.
 */

import { API_BASE, request } from './http';
import type { GeoJsonDocument, JobStatus, OptimizationMode, PipelineResult } from './types';

/** Форматы выгрузки: полётное задание или отчёт, расписание — CSV (docs/export_format.md). */
export type ExportFormat = 'geojson' | 'kml' | 'schedule';

/** Алгоритм распределения: по умолчанию — эвристика с поиском; baseline — точка сравнения. */
export type Allocator = 'local_search' | 'constructive' | 'baseline';

/**
 * Поставить сценарий в расчёт.
 *
 * @param scenarioId идентификатор сохранённого сценария
 * @param mode режим оптимизации: минимум срока или минимум ресурса (заказчик, №23)
 * @param allocator алгоритм распределения; без него — по умолчанию сервера
 */
export async function startPlanning(
  scenarioId: string,
  mode: OptimizationMode,
  allocator?: Allocator,
): Promise<JobStatus> {
  const query = new URLSearchParams({ mode });
  if (allocator) query.set('allocator', allocator);
  return request<JobStatus>(`/scenarios/${encodeURIComponent(scenarioId)}/plan?${query}`, {
    method: 'POST',
  });
}

export async function getJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`/jobs/${jobId}`);
}

/** Все задачи расчёта, новые первыми: и этого запуска сервера, и сохранённые прежде. */
export async function listJobs(): Promise<JobStatus[]> {
  return (await request<{ jobs: JobStatus[] }>('/jobs')).jobs;
}

/** Полный результат прогона: все стадии конвейера, а не только итог. */
export async function getResult(jobId: string): Promise<PipelineResult> {
  return request<PipelineResult>(`/jobs/${jobId}/result`);
}

/** Выгрузка в GeoJSON — её же рисует карта: план или отчёт о невыполнимости. */
export async function fetchMissionGeoJson(jobId: string): Promise<GeoJsonDocument> {
  return request<GeoJsonDocument>(`/jobs/${jobId}/export?format=geojson`);
}

/**
 * Ссылка на выгрузку. Скачивание — задача браузера, не приложения.
 *
 * @param board идентификатор борта — его индивидуальное задание; без него — весь план
 */
export function exportUrl(jobId: string, format: ExportFormat, board?: string): string {
  const query = new URLSearchParams({ format });
  if (board) {
    query.set('board', board);
  }
  return `${API_BASE}/jobs/${jobId}/export?${query}`;
}

/** Весь комплект файлов расчёта одним архивом ZIP: обзор и задания бортов. */
export function packageUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/package`;
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
