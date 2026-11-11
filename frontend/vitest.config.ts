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
    // Vitest blanks CSS imports by default; src/styleTokens.test.ts reads the stylesheet's text to check the tokens.
    css: { include: [/styles\/app\.css/] },
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/main.tsx', 'src/**/*.d.ts'],
      reporter: ['text-summary', 'json-summary'],
      // Floors sit ~1-2 points under the measured coverage (2026-11-11, after the Project Builder refactor:
      // statements 84.6, branches 77.9, functions 82.7, lines 87.0) so a regression fails CI;
      // raise them as coverage improves.
      thresholds: { statements: 83, branches: 76, functions: 81, lines: 85 },
    },
  },
});
