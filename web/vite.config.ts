import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Dev server on 3010 (strict). /api is proxied to the backend so cookies stay same-origin (no CORS).
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8005';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3010,
    strictPort: true,
    host: '127.0.0.1',
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false, ws: false },
    },
  },
  preview: { port: 3010, strictPort: true, host: '127.0.0.1', proxy: { '/api': { target: API_TARGET } } },
  build: { sourcemap: false, target: 'es2022' },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
