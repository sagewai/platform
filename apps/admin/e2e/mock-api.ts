/**
 * E2E test helpers — real backend, minimal browser-side mocks.
 *
 * The Playwright config starts both the backend (port 8000) and the
 * frontend (port 3808) automatically. The backend uses in-memory
 * state so tests are fast and deterministic.
 *
 * We only mock two things from the browser side:
 *  1. Auth cookie — so the proxy doesn't redirect to /login
 *  2. SSE event stream — to avoid hanging connections
 */
import type { Page } from '@playwright/test';

/**
 * Set the auth cookie from the real backend.
 * Call `loginAndGetToken()` first to get a valid token.
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
 * Call this once in beforeAll to bootstrap the backend state.
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
 * Authenticate the browser context — login fresh to get a valid
 * token (the backend only keeps one active token at a time).
 */
export async function authenticate(page: Page) {
  const { token } = await setupAndLogin();
  await setAuthCookie(page, token);

  // Mock SSE stream to avoid hanging connections
  await page.route('**/admin/events/stream', (route) =>
    route.fulfill({ body: '', contentType: 'text/event-stream' }),
  );
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
