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
 * исключил (docs/customer_qa.md, №8, №12). Сценарии приходят файлами через
 * API; правятся только параметры задания (E10), загрузка файлов в вебе —
 * заглушка (долг №29).
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { OptimizationMode } from '../api';
import { useScenarioFiles, useScenarioLayers, useUploads } from '../features/data';
import { FleetPage, fleetCatalog, parkToFleetRows, usePark, userVehicles } from '../features/fleet';
import { MapView } from '../features/map/MapView';
import { allLayers, resetPlanLayers, type MapFilter } from '../features/map/layers';
import {
  PlanningForm,
  usePlanning,
  useScenarioOptions,
  useScenarios,
  useVehicles,
  type ParkFleet,
} from '../features/planning';
import { ResultPanel } from '../features/result';
import { SchedulePage } from '../features/schedule/SchedulePage';
import { AppHeader } from './AppHeader';
import { MapOverlay } from './MapOverlay';
import { useResizablePanel } from './useResizablePanel';
import { useTab } from './useTab';
import styles from './App.module.css';

/** Режим по умолчанию — тот же, что в core/schemas.py. */
const DEFAULT_MODE: OptimizationMode = 'min_makespan';

export default function App() {
  const { tab, open } = useTab();
  const { scenarios, rejected, error: loadError } = useScenarios();
  const { vehicles, error: catalogError } = useVehicles();
  const park = usePark();
  const uploads = useUploads();
  const [chosenScenarioId, setChosenScenarioId] = useState('');
  const [mode, setMode] = useState<OptimizationMode>(DEFAULT_MODE);
  const [filter, setFilter] = useState<MapFilter>(allLayers);
  const planPage = useRef<HTMLDivElement | null>(null);
  const splitter = useResizablePanel(planPage);

  // Пока пользователь ничего не выбрал, берётся первый сценарий из списка.
  const scenarioId = chosenScenarioId || scenarios[0]?.id || '';
  const draft = useScenarioOptions(scenarioId);
  const { layers, error: layersError } = useScenarioLayers(scenarioId);
  const { files, error: filesError } = useScenarioFiles(scenarioId);
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
          {run && <ResultPanel run={run} onOpenSchedule={() => open('schedule')} />}
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
            uploads={uploads.files}
            onUpload={uploads.add}
            onRemoveUpload={uploads.remove}
            boards={run ? (run.document.properties?.boards ?? []) : null}
          />
        </main>
      </div>

      {tab === 'schedule' && (
        <main className={styles.page}>
          <SchedulePage
            run={run}
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
            scenarioId={scenarioId}
            scenarioFleet={draft.scenario?.fleet ?? null}
          />
        </main>
      )}
    </div>
  );
}
