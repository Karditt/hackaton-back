import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchPark, storePark, type Vehicle } from '../../api';
import {
  asPark,
  editCatalogPayloads,
  editCatalogSpec,
  loadPark,
  resetCatalog,
  resetCatalogModel,
  savePark,
  type CustomModel,
  type CustomPayload,
  type Park,
  type ParkBoard,
  type SpecKey,
} from './park';

/** Пауза перед отправкой парка на сервер: правка поля — не запрос на каждую букву. */
const SAVE_DELAY_MS = 500;

/**
 * Парк техники и правки каталога (E10, долг №50).
 *
 * Хранится на сервере (`GET/PUT /park`): тот же парк на любом компьютере и в
 * любом браузере. Копия — в localStorage: с неё страница начинает, пока
 * сервер отвечает, и на ней живёт, если сервер недоступен. Сервер пуст —
 * ему уходит то, что было в браузере. Правки ТТХ в расчёт идут вместе со
 * сценарием (`Scenario.vehicles`).
 */
export function usePark() {
  const [park, setPark] = useState<Park>(loadPark);
  const [persisted, setPersisted] = useState(true);
  // Пока не ясно, что лежит на сервере, парк туда не отправляется: иначе
  // пустой парк нового браузера затёр бы серверный.
  const synced = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetchPark()
      .then((stored) => {
        if (cancelled) return;
        const fromServer = stored === null ? null : asPark(stored);
        if (fromServer) setPark(fromServer);
        synced.current = true;
        if (!fromServer) void storePark(loadPark()).catch(() => setPersisted(false));
      })
      .catch(() => {
        // Сервер недоступен — работаем на копии браузера.
        synced.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const local = savePark(park);
    if (!synced.current) {
      setPersisted(local);
      return;
    }
    const timer = window.setTimeout(() => {
      storePark(park)
        .then(() => setPersisted(true))
        .catch(() => setPersisted(local));
    }, SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [park]);

  const editSpec = useCallback((vehicle: Vehicle, key: SpecKey, value: string) => {
    setPark((current) => editCatalogSpec(current, vehicle, key, value));
  }, []);

  const editPayloads = useCallback((vehicle: Vehicle, payloadIds: string[]) => {
    setPark((current) => editCatalogPayloads(current, vehicle, payloadIds));
  }, []);

  const resetModel = useCallback((id: string) => {
    setPark((current) => resetCatalogModel(current, id));
  }, []);

  /** Вернуть исходный каталог: снять все правки ТТХ и вернуть скрытые модели. */
  const resetAll = useCallback(() => {
    setPark((current) => resetCatalog(current));
  }, []);

  /** Выключить модель каталога из работы или вернуть её. */
  const setHidden = useCallback((id: string, hidden: boolean) => {
    setPark((current) => ({
      ...current,
      hidden: hidden
        ? [...current.hidden.filter((item) => item !== id), id]
        : current.hidden.filter((item) => item !== id),
    }));
  }, []);

  const addBoards = useCallback((boards: ParkBoard[]) => {
    setPark((current) => ({ ...current, boards: [...current.boards, ...boards] }));
  }, []);

  const updateBoard = useCallback((id: string, patch: Partial<ParkBoard>) => {
    setPark((current) => ({
      ...current,
      boards: current.boards.map((board) => (board.id === id ? { ...board, ...patch } : board)),
    }));
  }, []);

  const removeBoard = useCallback((id: string) => {
    setPark((current) => ({
      ...current,
      boards: current.boards.filter((board) => board.id !== id),
    }));
  }, []);

  /** Все борта в строй или все из строя. */
  const setAllActive = useCallback((active: boolean) => {
    setPark((current) => ({
      ...current,
      boards: current.boards.map((board) => ({ ...board, active })),
    }));
  }, []);

  const addModel = useCallback((model: CustomModel) => {
    setPark((current) => ({ ...current, models: [...current.models, model] }));
  }, []);

  const updateModel = useCallback((id: string, patch: Partial<CustomModel>) => {
    setPark((current) => ({
      ...current,
      models: current.models.map((model) => (model.id === id ? { ...model, ...patch } : model)),
    }));
  }, []);

  /** Удалить свою модель. Борта этой модели не удаляются — вызывающий это проверяет. */
  const removeModel = useCallback((id: string) => {
    setPark((current) => ({
      ...current,
      models: current.models.filter((model) => model.id !== id),
    }));
  }, []);

  const addPayload = useCallback((payload: CustomPayload) => {
    setPark((current) => ({ ...current, payloads: [...current.payloads, payload] }));
  }, []);

  const updatePayload = useCallback((id: string, patch: Partial<CustomPayload>) => {
    setPark((current) => ({
      ...current,
      payloads: current.payloads.map((payload) =>
        payload.id === id ? { ...payload, ...patch } : payload,
      ),
    }));
  }, []);

  /** Удалить свою нагрузку — из моделей, правок каталога и с бортов, где она стояла. */
  const removePayload = useCallback((id: string) => {
    setPark((current) => ({
      ...current,
      edits: Object.fromEntries(
        Object.entries(current.edits).map(([model, edit]) => [
          model,
          edit.payloadIds
            ? { ...edit, payloadIds: edit.payloadIds.filter((item) => item !== id) }
            : edit,
        ]),
      ),
      boards: current.boards.map((board) => ({
        ...board,
        payloadIds: board.payloadIds.filter((item) => item !== id),
      })),
      models: current.models.map((model) => ({
        ...model,
        payloadIds: model.payloadIds.filter((item) => item !== id),
      })),
      payloads: current.payloads.filter((payload) => payload.id !== id),
    }));
  }, []);

  return {
    park,
    persisted,
    addBoards,
    updateBoard,
    removeBoard,
    setAllActive,
    addModel,
    updateModel,
    removeModel,
    addPayload,
    updatePayload,
    removePayload,
    editSpec,
    editPayloads,
    resetModel,
    resetAll,
    setHidden,
  };
}

export type ParkState = ReturnType<typeof usePark>;
