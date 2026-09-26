import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, RefObject } from 'react';

/** Ширина панели по умолчанию, пиксели — как `--panel-width` в токенах. */
const DEFAULT_WIDTH_PX = 360;

/** Уже панели поля формы и флот не помещаются. */
const MIN_PANEL_PX = 300;

/** Шире панели — пустое место; карта нужнее. */
const MAX_PANEL_PX = 600;

/**
 * Уже карты не видно области: поверх неё ещё панель «Данные и слои» шириной
 * 320 пикселей, и карте остаётся хотя бы столько же рядом с ней.
 */
const MIN_MAP_PX = 560;

/** Шаг перемещения границы стрелками клавиатуры, пиксели. */
const KEY_STEP_PX = 16;

/** Ширина панели запоминается в браузере — удобство одного пользователя. */
const STORAGE_KEY = 'geoscan.panelWidth';

function stored(): number {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    return Number.isFinite(value) && value > 0 ? value : DEFAULT_WIDTH_PX;
  } catch {
    return DEFAULT_WIDTH_PX;
  }
}

/**
 * Подвижная граница между панелью и картой.
 *
 * Панель не уже `MIN_PANEL_PX` и не шире `MAX_PANEL_PX`, а карте всегда
 * остаётся не меньше `MIN_MAP_PX`: на узком окне побеждает карта. Граница
 * двигается мышью, стрелками с клавиатуры (Home/End — к краям), двойной
 * щелчок возвращает ширину по умолчанию. Ширина запоминается в браузере.
 *
 * @param container страница, внутри которой лежат панель и карта
 */
export function useResizablePanel(container: RefObject<HTMLElement | null>) {
  const [width, setWidth] = useState(stored);
  const [dragging, setDragging] = useState(false);
  const [available, setAvailable] = useState(() => window.innerWidth);
  const startRef = useRef<{ x: number; width: number } | null>(null);

  // Пределы зависят от ширины окна: у узкого окна панель уступает карте.
  const max = Math.max(MIN_PANEL_PX, Math.min(MAX_PANEL_PX, available - MIN_MAP_PX));
  const clamp = useCallback(
    (value: number) => Math.round(Math.min(max, Math.max(MIN_PANEL_PX, value))),
    [max],
  );
  const shown = clamp(width);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    // Спрятанная вкладка имеет нулевую ширину — это не повод сужать панель.
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setAvailable(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [container]);

  useEffect(() => {
    if (dragging) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, String(width));
    } catch {
      // Хранилище недоступно — ширина живёт до перезагрузки.
    }
  }, [width, dragging]);

  const onPointerDown = (event: PointerEvent<HTMLElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    startRef.current = { x: event.clientX, width: shown };
    setDragging(true);
  };

  const onPointerMove = (event: PointerEvent<HTMLElement>) => {
    const start = startRef.current;
    if (!start) return;
    setWidth(clamp(start.width + event.clientX - start.x));
  };

  const onPointerUp = (event: PointerEvent<HTMLElement>) => {
    event.currentTarget.releasePointerCapture(event.pointerId);
    startRef.current = null;
    setDragging(false);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const moves: Record<string, number> = {
      ArrowLeft: shown - KEY_STEP_PX,
      ArrowRight: shown + KEY_STEP_PX,
      Home: MIN_PANEL_PX,
      End: max,
    };
    if (event.key in moves) {
      event.preventDefault();
      setWidth(clamp(moves[event.key]));
    }
  };

  return {
    width: shown,
    dragging,
    separatorProps: {
      role: 'separator',
      'aria-orientation': 'vertical' as const,
      'aria-label': 'Граница между панелью и картой',
      'aria-valuemin': MIN_PANEL_PX,
      'aria-valuemax': max,
      'aria-valuenow': shown,
      tabIndex: 0,
      title: 'Перетащите, чтобы изменить ширину панели. Двойной щелчок — ширина по умолчанию',
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onKeyDown,
      onDoubleClick: () => setWidth(DEFAULT_WIDTH_PX),
    },
  };
}
