import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    host: true,
    // Same-origin in production (docs/ARCHITECTURE.md §2.1); in development the
    // dev server proxies instead, so the browser still sees one origin and the
    // session cookie keeps SameSite=Strict.
    //
    // The target has to be configurable: in Compose the backend is another
    // container, so `localhost` from inside this one is this one. Compose sets
    // VITE_DEV_PROXY_TARGET=http://backend:8000. It was declared there from
    // M1.1 but never read, which went unnoticed until M1.3 gave the SPA an API
    // call to make — the proxy failed with ECONNREFUSED.
    proxy: Object.fromEntries(
      ['/api', '/healthz', '/readyz'].map((path) => [
        path,
        {
          target: process.env.VITE_DEV_PROXY_TARGET ?? 'http://localhost:8000',
          changeOrigin: false,
        },
      ]),
    ),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/test/**', 'src/main.tsx', 'src/**/*.test.{ts,tsx}'],
    },
  },
});
