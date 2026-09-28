import { useState } from 'react';
import type { Scenario, Vehicle } from '../../api';
import { Notice } from '../../shared/ui/Notice';
import { FleetEditor, type ParkFleet } from './FleetEditor';
import { SURVEY_TYPES, customScenarioId, type ScenarioOptions } from './scenarioOptions';
import styles from './PlanningForm.module.css';

type Props = {
  /** Сохранённый сценарий: источник геометрии и площадок. */
  scenario: Scenario;
  options: ScenarioOptions;
  onChange: (options: ScenarioOptions) => void;
  onReset: () => void;
  edited: boolean;
  vehicles: Vehicle[];
  disabled: boolean;
  /** Борта парка в строю — источник флота (вкладка «Техника»). */
  parkFleet?: ParkFleet | null;
};

/**
 * Панель параметров сценария (E10).
 *
 * Правится только то, что заказчик называет параметрами задания. Геометрия —
 * область, зоны, препятствия, площадки — не редактируется: заказчик это
 * исключил (№8, №12), она берётся из выбранного сценария как есть.
 *
 * Правки не трогают исходный сценарий: при расчёте уходит производный, с
 * суффиксом в идентификаторе.
 */
export function ScenarioOptionsForm(props: Props) {
  const { options, onChange, scenario, disabled } = props;
  const set = (patch: Partial<ScenarioOptions>) => onChange({ ...options, ...patch });
  // Своё состояние, а не атрибут `open`: иначе React возвращал бы раздел
  // раскрытым при каждой перерисовке панели.
  const [open, setOpen] = useState(true);

  return (
    <details
      className={styles.params}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className={styles.summary}>
        Параметры сценария{props.edited && <span className={styles.badge}>правлены</span>}
      </summary>

      <label className={styles.field}>
        Тип съёмки
        <select
          className={styles.control}
          value={options.survey_type}
          onChange={(event) =>
            set({ survey_type: event.target.value as ScenarioOptions['survey_type'] })
          }
          disabled={disabled}
        >
          {SURVEY_TYPES.map(([type, label]) => (
            <option key={type} value={type}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <label className={styles.field}>
        GSD, м/пиксель
        <input
          className={styles.control}
          type="number"
          min={0}
          step={0.005}
          value={options.gsd_m}
          onChange={(event) => set({ gsd_m: event.target.value })}
          disabled={disabled}
        />
        <span className={styles.hint}>
          целевое разрешение на местности — по нему считается высота съёмки
        </span>
      </label>

      <fieldset className={styles.field}>
        <legend>Ветер</legend>
        <div className={styles.pairGrid}>
          <label className={styles.inline}>
            м/с
            <input
              className={styles.number}
              type="number"
              min={0}
              step={0.5}
              value={options.wind_speed_mps}
              onChange={(event) => set({ wind_speed_mps: event.target.value })}
              disabled={disabled}
            />
          </label>
          <label className={styles.inline}>
            °, откуда
            <input
              className={styles.number}
              type="number"
              min={0}
              max={360}
              step={5}
              value={options.wind_direction_deg}
              onChange={(event) => set({ wind_direction_deg: event.target.value })}
              disabled={disabled}
            />
          </label>
        </div>
        <span className={styles.hint}>
          направление — метеорологическое: 0° — ветер с севера. В расход не входит; влияет на
          выполнимость и на направление галсов — поперёк ветра борт держит скорость выше
        </span>
      </fieldset>

      <label className={styles.field}>
        Коридор высот, м над землёй
        <input
          className={styles.control}
          type="number"
          min={0}
          step={10}
          value={options.max_altitude_agl_m}
          onChange={(event) => set({ max_altitude_agl_m: event.target.value })}
          disabled={disabled}
        />
        <span className={styles.hint}>верхняя граница всех высот плана</span>
      </label>

      <label className={styles.field}>
        Начало работ, UTC
        <input
          className={styles.control}
          type="datetime-local"
          value={options.start_time}
          onChange={(event) => set({ start_time: event.target.value })}
          disabled={disabled}
        />
        <span className={styles.hint}>
          пусто — время в плане относительное, «T+чч:мм:сс»; окна временных зон тогда не
          проверяются
        </span>
      </label>

      <label className={styles.field}>
        Снимать при Солнце не ниже, °
        <input
          className={styles.control}
          type="number"
          min={0}
          max={60}
          step={1}
          placeholder="не учитывать"
          value={options.min_sun_elevation_deg}
          onChange={(event) => set({ min_sun_elevation_deg: event.target.value })}
          disabled={disabled}
        />
        <span className={styles.hint}>
          норма аэрофотосъёмки — 25° на цветную, 20° на черно-белую. Галсы — только в окнах по
          Солнцу, перелёты — в любое время; нужно начало работ
        </span>
      </label>

      <div className={styles.pairGrid}>
        <label className={styles.inline}>
          Дедлайн, ч
          <input
            className={styles.number}
            type="number"
            min={0}
            step={0.5}
            placeholder="нет"
            value={options.deadline_h}
            onChange={(event) => set({ deadline_h: event.target.value })}
            disabled={disabled}
          />
        </label>
        <label className={styles.inline}>
          Подготовка, мин
          <input
            className={styles.number}
            type="number"
            min={0}
            step={1}
            placeholder="по умолч."
            title="Межвылетная подготовка между вылетами одного борта"
            value={options.turnaround_min}
            onChange={(event) => set({ turnaround_min: event.target.value })}
            disabled={disabled}
          />
        </label>
      </div>

      <FleetEditor
        rows={options.fleet}
        vehicles={props.vehicles}
        sites={scenario.sites}
        onChange={(fleet) => set({ fleet })}
        disabled={disabled}
        parkFleet={props.parkFleet}
      />

      {props.edited && (
        <Notice tone="caution">
          Параметры правлены. Расчёт пойдёт по копии сценария{' '}
          <code>{customScenarioId(scenario.id ?? 'scenario')}</code>; исходный не меняется.{' '}
          <button type="button" className={styles.linkButton} onClick={props.onReset}>
            вернуть исходные
          </button>
        </Notice>
      )}
    </details>
  );
}
