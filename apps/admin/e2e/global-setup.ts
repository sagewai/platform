/**
 * Playwright global setup — runs once before all tests.
 *
 * Resets the backend state, runs the setup wizard via API,
 * and stores credentials for all subsequent tests.
 */

import { setupAndLogin, resetBackendState } from './mock-api';
import { writeFileSync } from 'fs';
import { join } from 'path';

const AUTH_FILE = join(__dirname, '.auth-state.json');

export default async function globalSetup() {
  // Reset to fresh-install state
  await resetBackendState();

  // Wait a moment for the backend to notice the state file is gone
  // (it reads from disk on every request)
  await new Promise((r) => setTimeout(r, 500));

  // Run setup + login via API
  const result = await setupAndLogin({
    orgName: 'E2E Test Org',
    email: 'admin@e2e.test',
    password: 'E2ePass!123',
  });

  // Save token for tests to use
  writeFileSync(AUTH_FILE, JSON.stringify(result));
}
