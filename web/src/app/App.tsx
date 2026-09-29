/**
 * Корень интерфейса (E10/E11): вкладки и общий выбор пользователя. Логики
 * расчёта здесь нет — она в features/planning, а все числа приходят с сервера.
 *
 * Вкладки: «План и карта» — сценарий, параметры, расчёт, карта с исходными
 * данными и результатом; «Расписание» — диаграмма и таблицы во всю ширину;
 * «Техника» — парк бортов, модели с ТТХ, полезная нагрузка. Состояние общее:
 * расчёт, отбор карты и парк не теряются при переходе между вкладками.
 *
 * Ввода и редактирования геометрии на карте нет и не будет: заказчик это
 * исключил (docs/customer_qa.md, №8, №12). Сценарии приходят файлами:
 * загрузкой в менеджере данных (слои KML и параметры — через ядро, с отчётом
 * импорта) или из хранилища API; правятся только параметры задания (E10).
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { OptimizationMode } from '../api';
import {
  NewScenarioForm,
  useNewScenario,
  useScenarioFiles,
  useScenarioLayers,
  useUploads,
} from '../features/data';
import { FleetPage, fleetCatalog, parkToFleetRows, usePark, userVehicles } from '../features/fleet';
import { HintPanel, useHint } from '../features/hints';
import { HistoryPanel } from '../features/history';
import { MapView } from '../features/map/MapView';
import { allLayers, resetPlanLayers, type MapFilter } from '../features/map/layers';
import { ParetoPanel, usePareto } from '../features/pareto';
import { SitesPanel, useSiteSearch } from '../features/sites';
import {
  addAdvisedSite,
  fleetSize,
  PlanningForm,
  usePlanning,
  useScenarioOptions,
  useScenarios,
  useVehicles,
  type ParkFleet,
} from '../features/planning';
import { ResultPanel } from '../features/result';
import { SchedulePage } from '../features/schedule/SchedulePage';
import { Notice } from '../shared/ui/Notice';
import { AppHeader } from './AppHeader';
import { MapOverlay } from './MapOverlay';
import { useResizablePanel } from './useResizablePanel';
import { useTab } from './useTab';
import styles from './App.module.css';

/** Режим по умолчанию — тот же, что в core/schemas.py. */
const DEFAULT_MODE: OptimizationMode = 'min_makespan';

export default function App() {
  const { tab, open } = useTab();
  const { scenarios, rejected, error: loadError, reload } = useScenarios();
  const { vehicles, error: catalogError } = useVehicles();
  const park = usePark();
  const [chosenScenarioId, setChosenScenarioId] = useState('');
  // Сценарий, собранный из файлов под тем же id, перечитывается заново.
  const [revision, setRevision] = useState(0);
  const [mode, setMode] = useState<OptimizationMode>(DEFAULT_MODE);
  const [filter, setFilter] = useState<MapFilter>(allLayers);
  // Совет «площадка» не принят: сервер отверг копию сценария.
  const [siteError, setSiteError] = useState<string | null>(null);
  const planPage = useRef<HTMLDivElement | null>(null);
  const splitter = useResizablePanel(planPage);

  // Пока пользователь ничего не выбрал, берётся первый сценарий из списка.
  const scenarioId = chosenScenarioId || scenarios[0]?.id || '';
  const draft = useScenarioOptions(scenarioId, revision);
  const { layers, error: layersError } = useScenarioLayers(scenarioId, revision);
  const { files, error: filesError } = useScenarioFiles(scenarioId, revision);
  const selectCreated = (createdId: string) => {
    reload();
    setChosenScenarioId(createdId);
    setRevision((current) => current + 1);
  };
  const uploads = useUploads({
    scenarioId,
    scenario: draft.scenario,
    sourceId: files?.source_id ?? null,
    onCreated: selectCreated,
  });
  const newScenario = useNewScenario({ template: draft.scenario, onCreated: selectCreated });
  const summary = scenarios.find((item) => item.id === scenarioId);
  const planningNote =
    summary?.planning_available === false
      ? (summary.planning_note ?? 'Сценарий открыт только для просмотра')
      : null;

  // Модели, из которых набирается флот: каталог с правками пользователя и свои.
  const catalog = useMemo(() => fleetCatalog(vehicles, park.park), [vehicles, park.park]);
  // Правленые и свои модели флота уходят в расчёт вместе со сценарием.
  const fleetIds = (draft.options?.fleet ?? []).map((row) => row.vehicle_id).join('|');
  const custom = useMemo(
    () => userVehicles(fleetIds.split('|').filter(Boolean), vehicles, park.park),
    [fleetIds, vehicles, park.park],
  );
  const planning = usePlanning({
    scenarioId,
    mode,
    scenario: draft.scenario,
    options: draft.options,
    vehicles: custom.vehicles,
    vehicleProblems: custom.problems,
  });
  const run = planning.run;
  const hint = useHint({
    scenarioId,
    scenario: draft.scenario,
    options: draft.options,
    vehicles: custom.vehicles,
    vehicleProblems: custom.problems,
  });
  const front = usePareto({
    scenarioId,
    scenario: draft.scenario,
    options: draft.options,
    vehicles: custom.vehicles,
    vehicleProblems: custom.problems,
  });
  const siteSearch = useSiteSearch({
    scenarioId,
    scenario: draft.scenario,
    options: draft.options,
    vehicles: custom.vehicles,
    vehicleProblems: custom.problems,
  });

  // Пул подсказки — флот панели параметров. Поле числа бортов с опечаткой —
  // пул не разобрать, об этом скажет и расчёт.
  const poolSize = useMemo(() => {
    try {
      return draft.options ? fleetSize(draft.options.fleet) : null;
    } catch {
      return null;
    }
  }, [draft.options]);
  // Сколько шёл последний расчёт этого сценария — для оценки ожидания подсказки.
  const lastRunTotalS =
    run && run.scenarioId.startsWith(scenarioId)
      ? Object.values(run.result.timings_s ?? {}).reduce((total, value) => total + value, 0)
      : 0;
  // Готовый план из истории или фронта времени расчёта может не нести.
  const lastRunS = lastRunTotalS >= 1 ? lastRunTotalS : null;

  // Новый расчёт — новый состав бортов: слои результата и выбор борта
  // возвращаются к «показать всё». Исходные данные, спрятанные в менеджере,
  // так и остаются спрятанными.
  const jobId = run?.jobId;
  useEffect(() => {
    setFilter((current) => resetPlanLayers(current));
  }, [jobId]);

  const parkFleet = useMemo<ParkFleet>(() => {
    const { rows, skipped } = parkToFleetRows(park.park, vehicles);
    return { rows, skipped: skipped.length };
  }, [park.park, vehicles]);

  const showBoard = (boardId: string) => {
    setFilter((current) => ({ ...current, board: boardId }));
    open('plan');
  };

  const sorties = run?.result.mission.schedule?.entries?.length;
  const boards = park.park.boards;
  const activeBoards = boards.filter((board) => board.active).length;

  return (
    <div className={styles.shell}>
      <AppHeader
        tab={tab}
        badges={{
          schedule: sorties ? String(sorties) : undefined,
          fleet: boards.length > 0 ? `${activeBoards}/${boards.length}` : undefined,
        }}
        scenarioName={summary?.name || scenarioId}
        planLabel={run?.label}
        phase={planning.phase}
        status={planning.status}
        viewOnly={planningNote !== null}
      />

      {/* Вкладка карты не размонтируется: иначе Leaflet терял бы вид и слои. */}
      <div
        ref={planPage}
        className={`${styles.planPage} ${splitter.dragging ? styles.resizing : ''}`}
        style={{ gridTemplateColumns: `${splitter.width}px auto 1fr` }}
        hidden={tab !== 'plan'}
      >
        <aside className={styles.panel}>
          <PlanningForm
            scenarios={scenarios}
            rejected={rejected}
            scenarioId={scenarioId}
            onScenarioChange={setChosenScenarioId}
            newScenario={
              <NewScenarioForm
                form={newScenario}
                templateName={summary?.name || scenarioId}
                disabled={planning.busy}
              />
            }
            mode={mode}
            onModeChange={setMode}
            scenario={draft.scenario}
            options={draft.options}
            onOptionsChange={draft.setOptions}
            onOptionsReset={draft.reset}
            optionsEdited={draft.edited}
            vehicles={catalog}
            parkFleet={parkFleet}
            planningNote={planningNote}
            onSubmit={planning.start}
            busy={planning.busy}
            status={planning.status}
            error={loadError ?? catalogError ?? draft.error ?? planning.error}
          />
          <HintPanel
            // Новый сценарий — новый вопрос: поля формы не переносятся.
            key={scenarioId}
            hint={hint}
            poolSize={poolSize}
            deadlineHours={draft.options?.deadline_h ?? ''}
            disabledNote={planningNote}
            lastRunS={lastRunS}
            vehicles={catalog}
            onShowPlan={(jobId, label) => void planning.showJob(jobId, label, 'hint')}
            planBusy={planning.busy}
          />
          <ParetoPanel
            // Новый сценарий — новый фронт: выбранная точка не переносится.
            key={`front-${scenarioId}`}
            front={front}
            disabledNote={planningNote}
            lastRunS={lastRunS}
            onShowPlan={(jobId, label) => void planning.showJob(jobId, label, 'pareto')}
            planBusy={planning.busy}
          />
          <SitesPanel
            key={`sites-${scenarioId}`}
            search={siteSearch}
            disabledNote={planningNote}
            lastRunS={lastRunS}
            onShowPlan={(jobId, label) => void planning.showJob(jobId, label, 'sites')}
            planBusy={planning.busy}
          />
          <HistoryPanel
            scenarios={scenarios}
            onShow={(jobId, label) => void planning.showJob(jobId, label, 'history')}
            busy={planning.busy}
          />
          {siteError && <Notice tone="error">{siteError}</Notice>}
          {run && (
            <ResultPanel
              run={run}
              vehicles={catalog}
              onOpenSchedule={() => open('schedule')}
              onAddSite={(lon, lat) => {
                setSiteError(null);
                addAdvisedSite(run.scenarioId, lon, lat)
                  .then(selectCreated)
                  .catch((cause: Error) => setSiteError(cause.message));
              }}
              busy={planning.busy}
            />
          )}
        </aside>

        <div className={styles.splitter} {...splitter.separatorProps} />

        <main className={styles.map}>
          <MapView document={run?.document ?? layers} filter={filter} visible={tab === 'plan'} />
          <MapOverlay
            scenarioId={scenarioId}
            layers={layers}
            layersError={layersError ?? filesError}
            files={files}
            filter={filter}
            onFilterChange={setFilter}
            uploads={uploads}
            boards={run ? (run.document.properties?.boards ?? []) : null}
          />
        </main>
      </div>

      {tab === 'schedule' && (
        <main className={styles.page}>
          <SchedulePage
            run={run}
            vehicles={catalog}
            busy={planning.busy}
            onGoPlan={() => open('plan')}
            onShowBoard={showBoard}
          />
        </main>
      )}

      {tab === 'fleet' && (
        <main className={styles.page}>
          <FleetPage
            vehicles={vehicles}
            catalogError={catalogError}
            park={park}
            scenarioFleet={draft.scenario?.fleet ?? null}
          />
        </main>
      )}
    </div>
  );
}
