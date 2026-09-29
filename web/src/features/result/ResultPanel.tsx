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
};

/** Предупреждения всех стадий конвейера в порядке их выполнения. */
function collectWarnings(result: PipelineResult): PlanWarning[] {
  return [
    ...(result.survey_plan.warnings ?? []),
    ...(result.decomposition?.warnings ?? []),
    ...(result.allocation?.warnings ?? []).filter((item) => item.severity !== 'error'),
    ...(result.coverage?.warnings ?? []),
    ...(result.mission.schedule?.warnings ?? []),
    ...(result.mission.warnings ?? []),
  ];
}

/**
 * Итог расчёта. Выполнимый план — метрики и расписание; невыполнимый — причины
 * и что добавить (заказчик, №33, №42). Частичных планов нет.
 */
export function ResultPanel({ run, vehicles, onOpenSchedule }: Props) {
  const { result } = run;
  const mission = result.mission;
  const feasible = mission.status === 'feasible';
  const warnings = collectWarnings(result);
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
      {run.label && run.origin !== 'history' && (
        <Notice tone="caution">
          {run.label} — не расчёт флота сценария: борта подобраны подсказкой, режим «минимум срока».
          Чтобы вернуться к флоту сценария — «Рассчитать».
        </Notice>
      )}
      {feasible ? (
        <Notice tone="success">План выполним: область покрыта целиком, краш-тест пройден.</Notice>
      ) : (
        <Notice tone="error">
          Задание невыполнимо. Частичный план не выдаётся — ниже причины и что добавить.
        </Notice>
      )}

      {feasible && mission.metrics && (
        <Section title="Метрики">
          <MetricsList result={result} vehicles={vehicles} />
        </Section>
      )}

      {feasible && mission.metrics && !mission.algorithm.startsWith('baseline') && (
        <Section title="Сравнение с baseline">
          {/* Новый план — новое сравнение: прежнее относилось к другому расчёту. */}
          <BaselineCompare key={run.jobId} run={run} />
        </Section>
      )}

      {!feasible && (
        <>
          <Section title={`Причины (${mission.reasons?.length ?? 0})`}>
            <WarningsList warnings={mission.reasons ?? []} />
          </Section>
          <Section title="Что добавить">
            <RecommendationsList items={mission.recommendations ?? []} />
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
          Стадии на заглушках: {stubStages.join(', ')}. Это не окончательный расчёт.
        </Notice>
      )}

      <Section title={`Предупреждения (${warnings.length})`}>
        <WarningsList warnings={warnings} />
      </Section>

      <ExportLinks jobId={run.jobId} feasible={feasible} boards={boards} />
    </>
  );
}
