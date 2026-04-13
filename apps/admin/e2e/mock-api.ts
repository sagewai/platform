/**
 * E2E test helpers — real backend, browser-based auth via storageState.
 *
 * The Playwright config starts both the backend (port 8000) and the
 * frontend (port 3808) automatically. The backend uses in-memory
 * state so tests are fast and deterministic.
 *
 * Auth is handled by the `setup` project (auth.setup.ts) which logs
 * in through the real browser UI and saves storageState to
 * .auth/user.json. All test projects inherit this state automatically
 * — no per-test cookie injection needed.
 */
import type { Page } from '@playwright/test';

/**
 * Set the auth cookie from the real backend.
 * Kept as a fallback for edge cases where storageState isn't enough.
 */
export async function setAuthCookie(page: Page, token: string) {
  await page.context().addCookies([
    {
      name: 'sagewai_auth',
      value: token,
      domain: 'localhost',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax' as const,
    },
  ]);
}

/**
 * Run the setup wizard via API and return an auth token.
 * Called by auth.setup.ts to bootstrap the backend before browser login.
 */
export async function setupAndLogin(opts?: {
  orgName?: string;
  email?: string;
  password?: string;
}): Promise<{ token: string; email: string; password: string }> {
  const email = opts?.email ?? 'admin@test.sagewai.dev';
  const password = opts?.password ?? 'TestPass123!';
  const orgName = opts?.orgName ?? 'E2E Test Org';

  // Check if setup is needed
  const statusRes = await fetch('http://localhost:8000/api/v1/setup/status');
  const status = await statusRes.json();

  if (status.setup_required) {
    // Run setup
    await fetch('http://localhost:8000/api/v1/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        org_name: orgName,
        org_slug: 'e2e-test',
        contact_email: email,
        timezone: 'UTC',
        app_name: 'E2E App',
        app_description: 'Automated testing',
        admin_name: 'E2E Admin',
        admin_email: email,
        admin_password: password,
      }),
    });
  }

  // Login
  const loginRes = await fetch('http://localhost:8000/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const loginData = await loginRes.json();

  return { token: loginData.access_token, email, password };
}

/**
 * Reset the backend to fresh-install state (for setup wizard tests).
 */
export async function resetBackendState() {
  const fs = await import('fs');
  const path = await import('path');
  const home = process.env.HOME ?? '/tmp';
  const stateFile = path.join(home, '.sagewai', 'admin-state.json');
  try {
    fs.unlinkSync(stateFile);
  } catch {
    // File doesn't exist — already clean
  }
}
