import { test, expect } from '@playwright/test';
import { setupAndLogin } from './mock-api';

// Login tests must start from an unauthenticated browser. The global
// auth.setup project saves an authenticated storageState that every test
// inherits by default — that state makes /login silently redirect to /
// and breaks every assertion here. Override with an empty storageState
// for this file only.
test.use({ storageState: { cookies: [], origins: [] } });

let credentials: { email: string; password: string };

test.beforeAll(async () => {
  // Must match the credentials auth.setup.ts used — by the time this file
  // runs, the admin account is already created and setupAndLogin() is a
  // pure login. Using the default `admin@test.sagewai.dev` here would hit
  // a 401 and the later "successful login" test would submit mismatched
  // credentials in the UI.
  credentials = await setupAndLogin({
    email: 'admin@e2e.test',
    password: 'E2ePass!123',
  });
});

test.describe('Login', () => {
  test('unauthenticated users are redirected to /login', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveURL(/\/login/);
  });

  test('login form has email, password, and sign-in button', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByPlaceholder('you@example.com')).toBeVisible();
    await expect(page.getByPlaceholder('••••••••')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();
  });

  test('successful login goes to dashboard', async ({ page }) => {
    await page.goto('/login');
    await page.getByPlaceholder('you@example.com').fill(credentials.email);
    await page.getByPlaceholder('••••••••').fill(credentials.password);
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page).toHaveURL('/', { timeout: 10_000 });
  });

  test('wrong password shows error', async ({ page }) => {
    await page.goto('/login');
    await page.getByPlaceholder('you@example.com').fill(credentials.email);
    await page.getByPlaceholder('••••••••').fill('wrongpassword');
    await page.getByRole('button', { name: 'Sign In' }).click();
    await expect(page.getByText(/Invalid email or password/)).toBeVisible();
  });

  test('shows forgot-password and sign-up links', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByText('Forgot password?')).toBeVisible();
    await expect(page.getByText('Sign up')).toBeVisible();
  });
});
