import { defineConfig } from '@playwright/test';

/**
 * Playwright configuration for the Sagewai admin panel.
 *
 * Starts both the Python backend (port 8000) and the Next.js frontend
 * (port 3808) automatically. No external services needed — the backend
 * uses in-memory state and file-based auth (~/.sagewai/admin-state.json).
 *
 * Run with:
 *   pnpm --filter @sagewai/admin test:e2e
 *   pnpm --filter @sagewai/admin test:e2e:ui   # interactive mode
 */
export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,  // Tests depend on shared backend state
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,  // Sequential — backend has single active token
  reporter: process.env.CI ? 'github' : 'list',
  timeout: 30_000,

  use: {
    baseURL: 'http://localhost:3808',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],

  webServer: [
    {
      // Backend — lightweight FastAPI with in-memory state.
      // Starts in ~2s. No Postgres/Redis required.
      command: 'uv run --package sagewai sagewai admin serve --host 0.0.0.0 --port 8000',
      port: 8000,
      reuseExistingServer: !process.env.CI,
      timeout: 15_000,
      cwd: '../../',  // monorepo root where uv.lock lives
    },
    {
      // Frontend — Next.js dev server pointed at the backend.
      command: 'NEXT_PUBLIC_ADMIN_API_URL=http://localhost:8000/admin pnpm exec next dev --port 3808',
      port: 3808,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
});
