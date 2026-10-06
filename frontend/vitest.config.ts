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
      // Floors sit ~2 points under the measured coverage (2026-10-05, after the plugin/inventory cutover:
      // statements 79.5, branches 72.5, functions 76.5, lines 82.3) so a regression fails CI;
      // raise them as coverage improves.
      thresholds: { statements: 77, branches: 70, functions: 74, lines: 80 },
    },
  },
});
