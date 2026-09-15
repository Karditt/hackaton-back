/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Адрес тайлов подложки. Задаётся для офлайн-фолбэка (см. src/map.ts). */
  readonly VITE_TILE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
