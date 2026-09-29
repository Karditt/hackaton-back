/**
 * Типы API.
 *
 * Формы контракта (всё, что описано в core/schemas.py) берутся ТОЛЬКО из
 * сгенерированного src/types/schema.ts и руками не пишутся (docs/dev/AGENT.md §2).
 * Здесь объявлены лишь транспортные обёртки, которые собирает сам слой API
 * и которых в контракте нет: сводка сценария, статус задачи, выгрузка GeoJSON.
 */

import type {
  HintKind,
  HintReport,
  ImportReport,
  LayerStats,
  OptimizationMode,
  PlanWarning,
} from '../types/schema';

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
  Stage,
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
  /** `area`, `zones`, `obstacles`, `sites` — слои KML; `params` — параметры задания; `scenario` — JSON. */
  kind: 'area' | 'zones' | 'obstacles' | 'sites' | 'params' | 'scenario';
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
  /** Алгоритм распределения плана: `baseline:…` — точка сравнения; null — плана ещё нет. */
  algorithm?: string | null;
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

/** Точка фронта «срок — ресурс» — `api/jobs.py`, `_point_public`. */
export type ParetoPoint = {
  /** Что это за план: «минимум срока», «минимум ресурса к сроку 3,20 ч»… */
  label: string;
  mode: OptimizationMode;
  /** Ограничение по сроку, секунды; null — без срока (края фронта). */
  deadline_s: number | null;
  feasible: boolean;
  /** Срок плана, секунды; null — план невыполним. */
  makespan_s: number | null;
  /** Ресурс плана — налёт плюс циклы, секунды. */
  resource_s: number | null;
  sortie_count: number | null;
  vehicle_count: number | null;
  /** Время расчёта точки, секунды. */
  elapsed_s: number;
  /** Другая точка не хуже по сроку и ресурсу и лучше хотя бы по одному. */
  dominated: boolean;
  /** Задача с планом точки: карта, итог, расписание, выгрузка. */
  plan_job_id: string | null;
};

/** Статус задачи фронта — `api/jobs.py`, `ParetoJob.public()`. */
export type ParetoStatus = {
  id: string;
  scenario_id: string;
  state: JobState;
  /** Доля готовности 0..1 — по числу готовых точек. */
  progress: number;
  /** Сколько планов будет: два края и промежуточные сроки. */
  expected_points: number;
  /** Готовые точки; по окончании — по возрастанию срока. */
  points: ParetoPoint[];
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

/** Проба подбора площадки — `api/jobs.py`, `_trial_public`. */
export type SiteTrial = {
  /** «как есть (…)» или «площадка для дальних 20% площади». */
  label: string;
  /** Место новой площадки, WGS84; null — расчёт «как есть». */
  position: { lon: number; lat: number } | null;
  /** Доля площади дальней работы, ради которой поставлен кандидат, 0..1. */
  share: number | null;
  feasible: boolean;
  /** Срок плана, секунды; null — план невыполним. */
  makespan_s: number | null;
  /** Ресурс плана — налёт плюс циклы, секунды. */
  resource_s: number | null;
  /** Время расчёта пробы, секунды. */
  elapsed_s: number;
  /** Задача с планом пробы: карта с предложенной площадкой, итог, выгрузка. */
  plan_job_id: string;
};

/** Статус подбора площадки — `api/jobs.py`, `SitesJob.public()`. */
export type SitesStatus = {
  id: string;
  scenario_id: string;
  state: JobState;
  progress: number;
  /** Сколько расчётов ожидается: «как есть» и по кандидату на долю площади. */
  expected_trials: number;
  /** Готовые пробы: первая — «как есть», дальше кандидаты. */
  trials: SiteTrial[];
  error: string | null;
  created_at: string;
  finished_at: string | null;
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
