/**
 * Playwright auth setup project — runs once before all test projects.
 *
 * Performs a real browser-based login so that Next.js client-side auth
 * (silentRefresh, cookies, isAuthenticated) works naturally in every
 * subsequent test. The resulting browser state is saved to .auth/user.json
 * and automatically injected into every test via storageState.
 *
 * This replaces the old approach of setting cookies via the API, which
 * broke because the Playwright-set cookie didn't propagate correctly
 * to client-side fetch calls (silentRefresh -> /api/v1/auth/refresh).
 */
import { test as setup, expect } from '@playwright/test';
import { setupAndLogin, resetBackendState } from './mock-api';
import { mkdirSync } from 'fs';
import { dirname } from 'path';

const AUTH_FILE = '.auth/user.json';

setup('authenticate via browser login', async ({ page }) => {
  // Reset backend to fresh-install state
  await resetBackendState();
  // Give the backend a moment to notice the state file is gone
  await page.waitForTimeout(500);

  // Ensure setup is complete and get credentials
  const { email, password } = await setupAndLogin({
    orgName: 'E2E Test Org',
    email: 'admin@e2e.test',
    password: 'E2ePass!123',
  });

  // Navigate to login and authenticate through the real UI
  await page.goto('/login');
  await expect(page.getByPlaceholder('you@example.com')).toBeVisible({
    timeout: 15_000,
  });

  await page.getByPlaceholder('you@example.com').fill(email);
  await page.getByPlaceholder('••••••••').fill(password);
  await page.getByRole('button', { name: 'Sign In' }).click();

  // Wait for redirect to dashboard — proves auth is fully working
  await expect(page).toHaveURL('/', { timeout: 15_000 });

  // Save browser state (cookies + localStorage) for all test projects
  mkdirSync(dirname(AUTH_FILE), { recursive: true });
  await page.context().storageState({ path: AUTH_FILE });
});
