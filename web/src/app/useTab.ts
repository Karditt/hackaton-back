import { useCallback, useEffect, useState } from 'react';

/** Вкладка приложения. */
export type Tab = 'plan' | 'schedule' | 'fleet';

export const TABS: [id: Tab, label: string][] = [
  ['plan', 'План и карта'],
  ['schedule', 'Расписание'],
  ['fleet', 'Техника'],
];

/** Вкладка из адреса `#/fleet`; неизвестная или пустая — «План». */
function tabFromHash(): Tab {
  const id = window.location.hash.replace(/^#\/?/, '');
  return TABS.some(([tab]) => tab === id) ? (id as Tab) : 'plan';
}

/**
 * Текущая вкладка живёт в адресе (`#/schedule`): перезагрузка страницы
 * оставляет на той же вкладке, «назад» в браузере возвращает на прежнюю.
 * Маршрутизатор не нужен — вкладок три, и они не вложены.
 */
export function useTab() {
  const [tab, setTab] = useState<Tab>(tabFromHash);

  useEffect(() => {
    const onChange = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const open = useCallback((next: Tab) => {
    window.location.hash = `/${next}`;
  }, []);

  return { tab, open };
}
