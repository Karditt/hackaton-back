import type { ParetoPoint } from '../../api';
import { decimal, hours } from '../../shared/format';
import styles from './pareto.module.css';

type Props = {
  points: ParetoPoint[];
  /** Выбранная точка — индекс в `points`; null — не выбрана. */
  selected: number | null;
  onSelect: (index: number) => void;
};

/** Размеры рисунка в единицах viewBox: ширина следует за панелью. */
const WIDTH = 320;
const HEIGHT = 190;
const MARGIN = { left: 42, right: 14, top: 14, bottom: 34 };
const TICKS = 4;

const SECONDS_IN_HOUR = 3600;

/**
 * Подпись деления в часах: «2,8». Узкая ось (меньше часа на весь размах) —
 * с двумя знаками, иначе соседние деления сливаются в одно число.
 */
function tickText(seconds: number, span: number): string {
  return decimal(seconds / SECONDS_IN_HOUR, span < SECONDS_IN_HOUR ? 2 : 1);
}

/** Границы оси с полями, чтобы крайние точки не легли на рамку. */
function domain(values: number[]): [number, number] {
  const low = Math.min(...values);
  const high = Math.max(...values);
  const pad = (high - low || high || 1) * 0.08;
  return [Math.max(0, low - pad), high + pad];
}

/**
 * Фронт «срок — ресурс» точками: по горизонтали — срок, по вертикали —
 * ресурс, оба в часах. Недоминируемые точки соединены линией — это и есть
 * выбор «быстрее или дешевле»; доминируемые — пустыми кружками. Щелчок по
 * точке выбирает её план. Ничего не считается: координаты — числа сервера.
 */
export function ParetoChart({ points, selected, onSelect }: Props) {
  const drawn = points
    .map((point, index) => ({ point, index }))
    .filter(({ point }) => point.makespan_s !== null && point.resource_s !== null);
  if (drawn.length === 0) {
    return <p className={styles.note}>Ни одного выполнимого плана — фронт не построить.</p>;
  }

  const [x0, x1] = domain(drawn.map(({ point }) => point.makespan_s as number));
  const [y0, y1] = domain(drawn.map(({ point }) => point.resource_s as number));
  const plotW = WIDTH - MARGIN.left - MARGIN.right;
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const x = (value: number) => MARGIN.left + ((value - x0) / (x1 - x0)) * plotW;
  const y = (value: number) => MARGIN.top + plotH - ((value - y0) / (y1 - y0)) * plotH;

  const front = drawn
    .filter(({ point }) => !point.dominated)
    .sort((a, b) => (a.point.makespan_s as number) - (b.point.makespan_s as number));
  const line = front
    .map(({ point }) => `${x(point.makespan_s as number)},${y(point.resource_s as number)}`)
    .join(' ');
  const ticks = Array.from({ length: TICKS + 1 }, (_, index) => index / TICKS);

  return (
    <svg
      className={styles.chart}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label="Фронт планов: срок по горизонтали, ресурс по вертикали"
    >
      {ticks.map((share) => {
        const gx = MARGIN.left + share * plotW;
        const gy = MARGIN.top + share * plotH;
        return (
          <g key={share}>
            <line className={styles.grid} x1={gx} x2={gx} y1={MARGIN.top} y2={MARGIN.top + plotH} />
            <line
              className={styles.grid}
              x1={MARGIN.left}
              x2={MARGIN.left + plotW}
              y1={gy}
              y2={gy}
            />
            <text className={styles.tick} x={gx} y={MARGIN.top + plotH + 13} textAnchor="middle">
              {tickText(x0 + share * (x1 - x0), x1 - x0)}
            </text>
            <text className={styles.tick} x={MARGIN.left - 5} y={gy + 3} textAnchor="end">
              {tickText(y1 - share * (y1 - y0), y1 - y0)}
            </text>
          </g>
        );
      })}
      <text className={styles.axis} x={MARGIN.left + plotW / 2} y={HEIGHT - 4} textAnchor="middle">
        срок, ч
      </text>
      <text
        className={styles.axis}
        x={11}
        y={MARGIN.top + plotH / 2}
        textAnchor="middle"
        transform={`rotate(-90 11 ${MARGIN.top + plotH / 2})`}
      >
        ресурс, ч
      </text>

      {front.length > 1 && <polyline className={styles.frontLine} points={line} />}

      {drawn.map(({ point, index }) => {
        const cx = x(point.makespan_s as number);
        const cy = y(point.resource_s as number);
        const isSelected = index === selected;
        return (
          <g
            key={index}
            className={styles.pointGroup}
            onClick={() => onSelect(index)}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onSelect(index);
            }}
          >
            <title>
              {`${point.label}\nсрок ${hours(point.makespan_s as number)} · ресурс ${hours(point.resource_s as number)}`}
            </title>
            {isSelected && <circle className={styles.halo} cx={cx} cy={cy} r={9} />}
            <circle
              className={point.dominated ? styles.dominated : styles.point}
              cx={cx}
              cy={cy}
              r={isSelected ? 6 : 5}
            />
          </g>
        );
      })}
    </svg>
  );
}
