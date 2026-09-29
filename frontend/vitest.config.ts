import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import pkg from './package.json';

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  // The API contract test imports the repo-root openapi.json (one level above the Vite root).
  server: { fs: { allow: ['..'] } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    exclude: ['e2e/**', 'node_modules/**'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/main.tsx', 'src/**/*.d.ts'],
      reporter: ['text-summary', 'json-summary'],
      // Floors sit ~2 points under the measured coverage (2026-09-29, after the test-hardening pass:
      // statements 71.6, branches 65.1, functions 67.8, lines 74.7) so a regression fails CI;
      // raise them as coverage improves.
      thresholds: { statements: 69, branches: 63, functions: 65, lines: 72 },
    },
  },
});
