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
    // Component tests only. Without this, vitest also collects `e2e/*.spec.ts`
    // and Playwright's `test.describe` throws inside the vitest runner — two
    // runners fighting over one glob, which reads as a broken suite rather
    // than as a configuration problem (M1.7).
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // Raised from vitest's 5 s. `ServerForm.test.tsx` types ~45 characters per test
    // through userEvent, which is timing-sensitive under the CPU contention of a
    // full parallel run: it started timing out intermittently once M2.2 added
    // five more test files to that contention (PROGRESS issue 59). A bound that
    // only a quiet machine meets is a flake, not a check.
    testTimeout: 15_000,
    coverage: {
      provider: 'v8',
      // `cobertura` as well as lcov, so scripts/check_coverage_diff.py reads
      // one format at both ends of the stack rather than one parser per
      // toolchain (OPEN-22's changed-line floor).
      reporter: ['text', 'lcov', 'cobertura'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/test/**', 'src/main.tsx', 'src/**/*.test.{ts,tsx}'],
    },
  },
});
