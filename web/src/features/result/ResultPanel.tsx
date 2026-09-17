import type { PipelineResult, PlanWarning } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { Section } from '../../shared/ui/Section';
import type { PlanningRun } from '../planning';
import { ExportLinks } from './ExportLinks';
import { MetricsList } from './MetricsList';
import { WarningsList } from './WarningsList';

type Props = { run: PlanningRun };

/** Предупреждения всех стадий конвейера в порядке их выполнения. */
function collectWarnings(result: PipelineResult): PlanWarning[] {
  return [
    ...(result.survey_plan.warnings ?? []),
    ...(result.coverage.warnings ?? []),
    ...(result.allocation.warnings ?? []),
    ...(result.mission.warnings ?? []),
  ];
}

/** Итог расчёта: метрики, признак заглушек, предупреждения, выгрузка. */
export function ResultPanel({ run }: Props) {
  const { result } = run;
  const warnings = collectWarnings(result);
  const stubStages = result.mission.stub_stages ?? [];

  return (
    <>
      <Section title="Метрики">
        <MetricsList result={result} />
      </Section>

      {stubStages.length > 0 && (
        <Notice tone="caution">
          План рассчитан с заглушками на стадиях: {stubStages.join(', ')}. Это не окончательный
          расчёт.
        </Notice>
      )}

      <Section title={`Предупреждения (${warnings.length})`}>
        <WarningsList warnings={warnings} />
      </Section>

      <ExportLinks jobId={run.jobId} />
    </>
  );
}
