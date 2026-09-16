import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Порт API берётся из переменной окружения: в docker compose это имя сервиса,
// при локальном запуске — localhost.
const apiTarget = process.env.VITE_API_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Прокси на API снимает вопрос CORS в разработке и делает один origin
    // для фронта и бэкенда — на демонстрации это на одну поломку меньше.
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  preview: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
