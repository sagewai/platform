import { test, expect } from '@playwright/test';
import { setupAndLogin } from './mock-api';

let credentials: { email: string; password: string };

test.beforeAll(async () => {
  credentials = await setupAndLogin();
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
