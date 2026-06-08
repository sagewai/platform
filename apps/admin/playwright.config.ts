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
  fullyParallel: false,  // Tests depend on shared backend state
  forbidOnly: !!process.env.CI,
  // One retry in CI absorbs the occasional SSE/timing flake without tripling
  // the runtime of a genuinely-failing test (the suite is deterministic now
  // that the shared-token-eviction bug is fixed; see state_file.py).
  retries: process.env.CI ? 1 : 0,
  // Skip snapshot comparisons in CI: baselines are platform-specific (darwin vs
  // linux) and are not committed. Visual regression is caught during local review.
  // See e2e/.gitignore — *.spec.ts-snapshots/ is excluded from the repo.
  ignoreSnapshots: !!process.env.CI,
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
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        storageState: '.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: /auth\.setup\.ts/,
    },
  ],

  webServer: [
    {
      // Backend — lightweight FastAPI with in-memory state.
      // Starts in ~2s. No Postgres/Redis required.
      // The admin UI (port 3808) calls the backend cross-origin with credentials,
      // so its origin must be in the CORS allowlist.
      // SAGEWAI_ADMIN_MAX_SESSION_TOKENS: raise the active-token cap far above
      // the default (200). The suite reuses one bootstrap token from the shared
      // storageState and fires hundreds of silentRefresh-driven token rotations
      // across a full run; at the default cap the bootstrap token is evicted
      // mid-suite and every later test redirects to /login. See state_file.py.
      command:
        'SAGEWAI_ADMIN_ALLOWED_ORIGINS=http://localhost:3808,http://127.0.0.1:3808 ' +
        'SAGEWAI_ADMIN_MAX_SESSION_TOKENS=100000 ' +
        'uv run --package sagewai sagewai admin serve --host 0.0.0.0 --port 8000',
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
