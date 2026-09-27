import { useState } from 'react';
import type { HintKind, HintQuestion, Vehicle } from '../../api';
import { hours, plural } from '../../shared/format';
import { Notice } from '../../shared/ui/Notice';
import { WarningsList } from '../result';
import { HintRunsTable } from './HintRunsTable';
import type { HintState } from './useHint';
import formStyles from '../planning/PlanningForm.module.css';
import styles from './hints.module.css';

/** Секунд в часе — перевод единиц показа: срок человек вводит в часах. */
const SECONDS_IN_HOUR = 3600;

const QUESTIONS: [kind: HintKind, label: string, hint: string][] = [
  ['boards', 'Срок при N бортах', 'сколько займёт работа, если взять N бортов пула'],
  ['deadline', 'Сколько бортов к сроку', 'наименьшее число бортов пула, которое успевает'],
  ['sweep', 'Срок при каждом N', 'от одного борта до всего пула: где новые борта уже не помогают'],
];

type Props = {
  hint: HintState;
  /** Размер пула — флота в панели параметров; null — число бортов не разобрать. */
  poolSize: number | null;
  /** Дедлайн из панели параметров, часы, — срок вопроса по умолчанию. */
  deadlineHours: string;
  /** Почему подсказку не спросить (сценарий только для просмотра); null — можно. */
  disabledNote: string | null;
  /** Сколько шёл последний расчёт этого сценария, секунды, — для оценки ожидания. */
  lastRunS: number | null;
  /** Каталог с правками — имена моделей, которых нет в пуле отчёта. */
  vehicles: Vehicle[];
  /** Показать план ответа как текущий расчёт: карта, метрики, расписание. */
  onShowPlan: (jobId: string, label: string) => void;
  /** Идёт расчёт или загрузка плана — план ответа пока не показать. */
  planBusy: boolean;
};

/** Число из поля формы; пусто — null, не число — NaN. */
function parseNumber(text: string): number | null {
  const trimmed = text.trim().replace(',', '.');
  return trimmed === '' ? null : Number(trimmed);
}

/** Вопрос из полей формы или причина, по которой его не задать. */
function buildQuestion(
  kind: HintKind,
  boardsText: string,
  deadlineText: string,
  poolSize: number,
): { question: HintQuestion } | { problem: string } {
  const deadlineHours = parseNumber(deadlineText);
  if (deadlineHours !== null && !(deadlineHours > 0)) {
    return { problem: 'Срок — число часов больше нуля' };
  }
  const deadlineS = deadlineHours === null ? undefined : deadlineHours * SECONDS_IN_HOUR;
  if (kind === 'deadline') {
    return deadlineS === undefined ? { problem: 'Задайте срок' } : { question: { deadlineS } };
  }
  if (kind === 'sweep') {
    return { question: { sweep: true, deadlineS } };
  }
  const boards = parseNumber(boardsText);
  if (boards === null) return { problem: 'Задайте число бортов' };
  if (!Number.isInteger(boards) || boards < 1 || boards > poolSize) {
    return { problem: `Число бортов — целое от 1 до ${poolSize}` };
  }
  return { question: { boards, deadlineS } };
}

/**
 * Сколько прогонов ждать — та же оценка, что у полосы прогресса сервера
 * (`api/jobs.py`, `_expected_runs`): весь пул, двоичный поиск и два соседа.
 */
function expectedRuns(kind: HintKind, poolSize: number): number {
  if (kind === 'boards') return 1;
  if (kind === 'sweep') return poolSize;
  return 1 + Math.ceil(Math.log2(Math.max(2, poolSize))) + 2;
}

/** @param seconds длительность, секунды: короткая — в секундах, длинная — в часах и минутах */
function waitText(seconds: number): string {
  return seconds < 90 ? `${Math.round(seconds)} с` : hours(seconds);
}

/** «5 бортов». */
function boardsText(count: number): string {
  return `${count} ${plural(count, ['борт', 'борта', 'бортов'])}`;
}

/**
 * Подсказка по флоту: флот сценария — пул, вопрос — срок при N бортах,
 * сколько бортов нужно к сроку или срок при каждом N. Ответ подтверждён
 * планом, и этот план можно открыть как обычный расчёт.
 */
export function HintPanel(props: Props) {
  const { hint, poolSize } = props;
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<HintKind>('deadline');
  const [boardsInput, setBoardsInput] = useState('');
  const [deadlineInput, setDeadlineInput] = useState<string | null>(null);
  // Пока срок не трогали, он берётся из панели параметров.
  const deadlineText = deadlineInput ?? props.deadlineHours;

  const size = poolSize ?? 0;
  const built =
    poolSize === null
      ? { problem: 'Число бортов во флоте не разобрать — проверьте панель параметров' }
      : size === 0
        ? { problem: 'Во флоте нет бортов' }
        : buildQuestion(kind, boardsInput, deadlineText, size);
  const blocked = hint.busy || props.disabledNote !== null || 'problem' in built;

  const runs = expectedRuns(kind, size);
  const runsText =
    kind === 'boards'
      ? 'Один прогон конвейера'
      : `Около ${runs} ${plural(runs, ['прогона', 'прогонов', 'прогонов'])} конвейера`;
  const waitHint =
    props.lastRunS !== null
      ? `Расчёт этого сценария шёл ${waitText(props.lastRunS)}: ждать около ${waitText(runs * props.lastRunS)}.`
      : 'На данных заказчика прогон идёт до минуты, и вопрос занимает минуты; на тестовых сценариях — секунды.';

  const answer = hint.answer;
  const report = answer?.status.report ?? null;
  const planJobId = answer?.status.plan_job_id ?? null;
  const names = new Map(
    (report?.pool ?? []).map((board) => [board.vehicle_id, board.vehicle_name]),
  );
  const modelName = (vehicleId: string) =>
    names.get(vehicleId) ??
    props.vehicles.find((vehicle) => vehicle.id === vehicleId)?.name ??
    vehicleId;
  const planLabel =
    report?.answer_boards != null
      ? `План подсказки: ${boardsText(report.answer_boards)}`
      : 'План подсказки';
  const elapsedS = hint.startedAt === null ? 0 : (Date.now() - hint.startedAt) / 1000;

  return (
    <details
      className={formStyles.params}
      open={open || hint.busy}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className={formStyles.summary}>
        Подсказка по флоту
        {hint.busy && <span className={styles.badgeRunning}>идёт</span>}
        {!hint.busy && answer && (
          <span className={hint.stale ? formStyles.badge : styles.badgeReady}>
            {hint.stale ? 'устарела' : 'есть ответ'}
          </span>
        )}
      </summary>
      <p className={styles.lead}>
        Флот сценария — пул доступной техники. На каждое число бортов считается настоящий план с
        краш-тестом, так что ответ подтверждён планом.
      </p>

      <fieldset className={formStyles.field}>
        <legend>Вопрос</legend>
        {QUESTIONS.map(([value, label, help]) => (
          <label key={value} className={formStyles.option}>
            <input
              type="radio"
              name="hint-kind"
              value={value}
              checked={kind === value}
              onChange={() => setKind(value)}
              disabled={hint.busy}
            />
            {label}
            <span className={formStyles.hint}>{help}</span>
          </label>
        ))}
      </fieldset>

      <div className={styles.fields}>
        {kind === 'boards' && (
          <label className={formStyles.inline}>
            N
            <input
              className={formStyles.number}
              type="number"
              min={1}
              max={size || undefined}
              step={1}
              placeholder={size ? `1–${size}` : ''}
              value={boardsInput}
              onChange={(event) => setBoardsInput(event.target.value)}
              disabled={hint.busy}
            />
          </label>
        )}
        <label className={formStyles.inline}>
          Срок, ч
          <input
            className={formStyles.number}
            type="number"
            min={0}
            step="any"
            placeholder={kind === 'deadline' ? 'обязателен' : 'не задан'}
            value={deadlineText}
            onChange={(event) => setDeadlineInput(event.target.value)}
            disabled={hint.busy}
          />
        </label>
      </div>
      {kind !== 'deadline' && (
        <p className={styles.note}>
          Срок необязателен: с ним ответ скажет, успевают ли. Пусто — дедлайн сценария, если он
          задан.
        </p>
      )}

      <p className={styles.note}>
        В пуле {boardsText(size)}. {runsText}, каждый — полный расчёт плана; если состав приходится
        чинить, прогонов больше. {waitHint}
      </p>

      <button
        type="button"
        className={styles.ask}
        disabled={blocked}
        title={props.disabledNote ?? ('problem' in built ? built.problem : undefined)}
        onClick={() => 'question' in built && void hint.ask(built.question)}
      >
        {hint.busy ? 'Считаю…' : 'Спросить'}
      </button>
      {!hint.busy && 'problem' in built && poolSize !== null && size > 0 && (
        <p className={styles.note}>{built.problem}</p>
      )}
      {props.disabledNote && <Notice tone="caution">{props.disabledNote}</Notice>}

      {hint.busy && (
        <div className={styles.progressBox} role="status">
          <progress className={styles.progress} value={hint.tick?.progress ?? 0} max={1} />
          <span>
            {hint.tick ? `${hint.status}: прогонов сделано ${hint.tick.runs_done}` : hint.status} ·{' '}
            {waitText(elapsedS)}
          </span>
        </div>
      )}
      {hint.error && <Notice tone="error">{hint.error}</Notice>}

      {answer && report && (
        <div className={styles.answer}>
          {hint.stale && (
            <Notice tone="caution">
              Параметры изменены после ответа — он про прежние. Спросите заново.
            </Notice>
          )}
          <p className={styles.answerText}>{report.answer}</p>
          <p className={styles.note}>
            Прогоны — в режиме «минимум срока» и без дедлайна сценария: срок сравнивается с итогом.
            Выделена строка ответа.
          </p>
          <HintRunsTable report={report} modelName={modelName} />
          {(report.warnings ?? []).length > 0 && (
            <div className={styles.warnings}>
              <WarningsList warnings={report.warnings ?? []} />
            </div>
          )}
          {planJobId ? (
            <button
              type="button"
              className={styles.showPlan}
              disabled={hint.stale || props.planBusy}
              title={hint.stale ? 'Ответ устарел — спросите заново' : undefined}
              onClick={() => props.onShowPlan(planJobId, planLabel)}
            >
              Показать план ответа на карте
            </button>
          ) : (
            <p className={styles.note}>Плана ответа нет: выполнимого состава не нашлось.</p>
          )}
        </div>
      )}
    </details>
  );
}
