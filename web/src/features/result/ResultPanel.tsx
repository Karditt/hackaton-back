import type { PipelineResult, PlanWarning } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { Section } from '../../shared/ui/Section';
import type { PlanningRun } from '../planning';
import { ExportLinks } from './ExportLinks';
import { MetricsList } from './MetricsList';
import { RecommendationsList } from './RecommendationsList';
import { ScheduleTable } from './ScheduleTable';
import { WarningsList } from './WarningsList';

type Props = { run: PlanningRun };

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
export function ResultPanel({ run }: Props) {
  const { result } = run;
  const mission = result.mission;
  const feasible = mission.status === 'feasible';
  const warnings = collectWarnings(result);
  const stubStages = mission.stub_stages ?? [];

  return (
    <>
      {feasible ? (
        <Notice tone="success">План выполним: область покрыта целиком, краш-тест пройден.</Notice>
      ) : (
        <Notice tone="error">
          Задание невыполнимо. Частичный план не выдаётся — ниже причины и что добавить.
        </Notice>
      )}

      {feasible && mission.metrics && (
        <Section title="Метрики">
          <MetricsList result={result} />
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
        <Section title={`Расписание (${mission.schedule.entries?.length ?? 0})`}>
          <ScheduleTable schedule={mission.schedule} />
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

      <ExportLinks
        jobId={run.jobId}
        feasible={feasible}
        boards={run.document.properties?.boards ?? []}
      />
    </>
  );
}
