/**
 * Типы API.
 *
 * Формы контракта (всё, что описано в core/schemas.py) берутся ТОЛЬКО из
 * сгенерированного src/types/schema.ts и руками не пишутся (AGENT.md §2).
 * Здесь объявлены лишь транспортные обёртки, которые собирает сам слой API
 * и которых в контракте нет: сводка сценария, статус задачи, выгрузка GeoJSON.
 */

import type { HintKind, HintReport, ImportReport, LayerStats, PlanWarning } from '../types/schema';

export type {
  AltitudeCorridor,
  FleetUnit,
  FlightPhase,
  HintBoard,
  HintKind,
  HintReport,
  HintRun,
  ImportReport,
  LayerStats,
  Mission,
  MissionMetrics,
  MissionStatus,
  OptimizationMode,
  Payload,
  PayloadKind,
  PipelineResult,
  PlanWarning,
  QualityRequirement,
  Recommendation,
  RecommendationKind,
  Scenario,
  Schedule,
  ScheduleEntry,
  Severity,
  Site,
  SurveyType,
  Vehicle,
  VehicleType,
  Wind,
} from '../types/schema';

/** Элемент списка `GET /scenarios`. */
export type ScenarioSummary = {
  id: string;
  name: string;
  /** false — сценарий открыт для просмотра, расчёт из интерфейса не запускается. */
  planning_available?: boolean;
  /** Почему расчёт недоступен — для пользователя. */
  planning_note?: string | null;
};

/** Исходный файл сценария — `GET /scenarios/{id}/files`. */
export type ScenarioFile = {
  /** `area`, `zones`, `obstacles` — слои KML; `params` — параметры задания; `scenario` — JSON. */
  kind: 'area' | 'zones' | 'obstacles' | 'params' | 'scenario';
  name: string;
  size_bytes: number;
  /** Что сделал импорт со слоем; у параметров и JSON — нет. */
  stats: LayerStats | null;
};

/** Исходники сценария: из каких файлов он собран. */
export type ScenarioFiles = {
  scenario_id: string;
  /** Сценарий, чьи файлы показаны: у копии с правками — исходный; null — файлов нет. */
  source_id: string | null;
  files: ScenarioFile[];
  area_km2?: number;
  warnings: PlanWarning[];
};

/** Ответ `POST /scenarios/import`: сохранённый сценарий и что импорт сделал со слоями. */
export type ImportedScenario = { id: string; name: string; report: ImportReport };

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

/** Статус задачи подсказки — `api/jobs.py`, `HintJob.public()`. */
export type HintStatus = {
  id: string;
  scenario_id: string;
  kind: HintKind;
  /** Вопрос: число бортов; null — вопрос не о нём. */
  boards: number | null;
  /** Срок из вопроса, секунды; null — не задан. */
  deadline_s: number | null;
  state: JobState;
  /** Доля готовности 0..1 — оценка по числу прогонов. */
  progress: number;
  /** Сколько прогонов конвейера уже сделано. */
  runs_done: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  /** Отчёт: прогоны по N и ответ фразой; null — ещё не готов. */
  report: HintReport | null;
  /** Задача расчёта с планом из ответа; null — ответа нет. */
  plan_job_id: string | null;
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

/**
 * Слои сценария до расчёта — `GET /scenarios/{id}/layers`
 * (`core/export/geojson.py`, `scenario_to_geojson`). Признаки те же, что у
 * слоёв сценария в выгрузке плана; в свойствах — число объектов по видам.
 */
export type ScenarioLayers = GeoJsonDocument & {
  properties: {
    scenario_id: string;
    name: string;
    /** Площадь области съёмки, км²; null — геометрия не переводится в метры. */
    area_km2: number | null;
    /** Число объектов по полю `kind`: частей области, зон, препятствий, площадок. */
    counts: Record<string, number>;
  };
};
