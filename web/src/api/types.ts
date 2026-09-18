/**
 * Типы API.
 *
 * Формы контракта (всё, что описано в core/schemas.py) берутся ТОЛЬКО из
 * сгенерированного src/types/schema.ts и руками не пишутся (AGENT.md §2).
 * Здесь объявлены лишь транспортные обёртки, которые собирает сам слой API
 * и которых в контракте нет: сводка сценария, статус задачи, выгрузка GeoJSON.
 */

export type {
  FlightPhase,
  Mission,
  MissionMetrics,
  MissionStatus,
  OptimizationMode,
  PipelineResult,
  PlanWarning,
  Recommendation,
  RecommendationKind,
  Schedule,
  ScheduleEntry,
  Severity,
} from '../types/schema';

/** Элемент списка `GET /scenarios`. */
export type ScenarioSummary = { id: string; name: string };

export type JobState = 'pending' | 'running' | 'done' | 'failed';

/** Статус фоновой задачи — `api/jobs.py`, `Job.public()`. */
export type JobStatus = {
  id: string;
  scenario_id: string;
  state: JobState;
  progress: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  mission_id: string | null;
  /** Итог расчёта: `feasible` или `infeasible`; невыполнимость — не сбой задачи. */
  mission_status: string | null;
};

export type GeoJsonFeature = {
  type: 'Feature';
  geometry: { type: string; coordinates: unknown };
  properties: Record<string, unknown>;
};

/** Выгрузка в WGS84 по RFC 7946 — её строит `core/export`, фронт только рисует. */
export type GeoJsonDocument = {
  type: 'FeatureCollection';
  features: GeoJsonFeature[];
  properties?: Record<string, unknown>;
};
