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

/** Файл хранилища, не прочитанный по контракту, — `GET /scenarios`, поле `rejected`. */
export type RejectedScenario = { file: string; reason: string };

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

/**
 * Борт плана — свойство `boards` выгрузки GeoJSON (`core/export/boards.py`,
 * `Board.summary()`). По `board_id` скачивается его индивидуальное задание.
 */
export type BoardSummary = {
  board_id: string;
  label: string;
  vehicle_id: string;
  vehicle_instance: number;
  sortie_count: number;
  flight_time_s: number;
  first_takeoff_s: number;
  last_landing_s: number;
};

/** Выгрузка в WGS84 по RFC 7946 — её строит `core/export`, фронт только рисует. */
export type GeoJsonDocument = {
  type: 'FeatureCollection';
  features: GeoJsonFeature[];
  properties?: Record<string, unknown> & { boards?: BoardSummary[] };
};
