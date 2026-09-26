import { useCallback, useState } from 'react';
import type { DataKindId } from './dataKinds';

/** Файл, выбранный кнопкой «Загрузить». */
export type UploadedFile = {
  id: string;
  kind: DataKindId;
  name: string;
  /** Размер, байты. */
  size: number;
};

/**
 * Загруженные файлы по видам данных.
 *
 * TODO(E10): заглушка, заменить на реальную реализацию. Файл только
 * запоминается в списке: на сервер он не уходит, на карте не рисуется и в
 * расчёт не попадает. Приём слоёв в API уже есть (`POST /scenarios/import`);
 * связать с ним — задача `e10-web-upload` (docs/current_state.md §2).
 * Интерфейс сам объявляет заглушку пометкой у каждого файла.
 */
export function useUploads() {
  const [files, setFiles] = useState<UploadedFile[]>([]);

  const add = useCallback((kind: DataKindId, picked: FileList | null) => {
    if (!picked || picked.length === 0) return;
    const added = Array.from(picked, (file, index) => ({
      id: `${Date.now()}-${index}-${file.name}`,
      kind,
      name: file.name,
      size: file.size,
    }));
    setFiles((current) => [...current, ...added]);
  }, []);

  const remove = useCallback((id: string) => {
    setFiles((current) => current.filter((file) => file.id !== id));
  }, []);

  return { files, add, remove };
}
