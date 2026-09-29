import type { PipelineResult, PlanWarning, Vehicle } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { Section } from '../../shared/ui/Section';
import type { PlanningRun } from '../planning';
import { BaselineCompare } from './BaselineCompare';
import { ExportLinks } from './ExportLinks';
import { MetricsList } from './MetricsList';
import { RecommendationsList } from './RecommendationsList';
import { WarningsList } from './WarningsList';
import styles from './result.module.css';

type Props = {
  run: PlanningRun;
  /** Каталог моделей — для имён в составе плана. */
  vehicles: Vehicle[];
  /** Открыть вкладку «Расписание» — диаграмма и таблица живут там во всю ширину. */
  onOpenSchedule: () => void;
  /** Принять проверенный совет «площадка»: копия сценария с ней. */
  onAddSite?: (lon: number, lat: number) => void;
  busy?: boolean;
};

/** Порядок показа: сначала то, что требует внимания, справки — в конце. */
const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 } as const;

/**
 * Предупреждения всех стадий конвейера: важные сверху, внутри уровня — в
 * порядке стадий.
 *
 * У выполнимого плана ошибок нет по определению: ошибка стадии (например,
 * борт без подходящей камеры исключён из пула) план не сломала, и
 * показывается она как «Внимание», а не как ошибка плана.
 */
function collectWarnings(result: PipelineResult, feasible: boolean): PlanWarning[] {
  const all = [
    ...(result.survey_plan.warnings ?? []),
    ...(result.decomposition?.warnings ?? []),
    ...(result.allocation?.warnings ?? []).filter((item) => item.severity !== 'error'),
    ...(result.coverage?.warnings ?? []),
    ...(result.mission.schedule?.warnings ?? []),
    ...(result.mission.warnings ?? []),
  ].map((item) =>
    feasible && item.severity === 'error' ? { ...item, severity: 'warning' as const } : item,
  );
  return all
    .map((item, index) => ({ item, index }))
    .sort(
      (a, b) =>
        SEVERITY_ORDER[a.item.severity ?? 'warning'] -
          SEVERITY_ORDER[b.item.severity ?? 'warning'] || a.index - b.index,
    )
    .map(({ item }) => item);
}

/**
 * Итог расчёта. Выполнимый план — метрики и расписание; невыполнимый — причины
 * и что добавить (заказчик, №33, №42). Частичных планов нет.
 */
export function ResultPanel({ run, vehicles, onOpenSchedule, onAddSite, busy }: Props) {
  const { result } = run;
  const mission = result.mission;
  const feasible = mission.status === 'feasible';
  const warnings = collectWarnings(result, feasible);
  const stubStages = mission.stub_stages ?? [];
  const boards = run.document.properties?.boards ?? [];

  return (
    <>
      {run.label && run.origin === 'history' && (
        <Notice tone="caution">
          {run.label} — сохранённый расчёт, параметры могли с тех пор измениться. Посчитать по
          текущим — «Рассчитать».
        </Notice>
      )}
      {run.label && run.origin === 'hint' && (
        <Notice tone="caution">
          {run.label}: борта подобраны подсказкой по флоту, критерий — минимум срока. Чтобы
          посчитать весь пул сценария — «Рассчитать».
        </Notice>
      )}
      {run.label && run.origin === 'sites' && (
        <Notice tone="caution">
          {run.label} — пробный план подбора площадки: новая площадка на карте подписана
          «Предложенная площадка», площадки бортам выбирал расчёт. Чтобы посчитать по параметрам
          задания — «Рассчитать».
        </Notice>
      )}
      {run.label && run.origin === 'pareto' && (
        <Notice tone="caution">
          {run.label} — точка фронта «срок — ресурс», срок задания не учитывался. Чтобы посчитать по
          параметрам задания — «Рассчитать».
        </Notice>
      )}
      {feasible ? (
        <Notice tone="success">
          План выполним: область покрыта целиком, проверка безопасности пройдена.
        </Notice>
      ) : (
        <Notice tone="error">
          Задание невыполнимо. Частичный план не выдаётся — ниже причины и что изменить.
        </Notice>
      )}

      {feasible && mission.metrics && (
        <Section title="Итог">
          <MetricsList result={result} vehicles={vehicles} />
        </Section>
      )}

      {feasible && mission.metrics && !mission.algorithm.startsWith('baseline') && (
        <Section title="Сравнение с равномерным делением">
          {/* Новый план — новое сравнение: прежнее относилось к другому расчёту. */}
          <BaselineCompare key={run.jobId} run={run} />
        </Section>
      )}

      {!feasible && (
        <>
          <Section title={`Причины (${mission.reasons?.length ?? 0})`}>
            <WarningsList warnings={mission.reasons ?? []} />
          </Section>
          <Section title="Что изменить">
            <RecommendationsList
              items={mission.recommendations ?? []}
              document={run.document}
              onAddSite={onAddSite}
              busy={busy}
            />
          </Section>
        </>
      )}

      {feasible && mission.schedule && (
        <Section title="Расписание">
          <p className={styles.scheduleLink}>
            {mission.schedule.entries?.length ?? 0} вылетов у {boards.length} бортов.{' '}
            <button type="button" className={styles.linkButton} onClick={onOpenSchedule}>
              Открыть диаграмму и таблицу →
            </button>
          </p>
        </Section>
      )}

      {stubStages.length > 0 && (
        <Notice tone="caution">
          Часть расчёта выполнена упрощённо ({stubStages.join(', ')}) — это предварительный план.
        </Notice>
      )}

      <Section title={`Замечания и справки (${warnings.length})`}>
        <WarningsList warnings={warnings} />
      </Section>

      <ExportLinks jobId={run.jobId} feasible={feasible} boards={boards} />
    </>
  );
}
