import { test, expect } from '@playwright/test';

test.describe('Dashboard', () => {
  test('renders dashboard with header', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible({ timeout: 10_000 });
  });

  test('shows TV Mode link', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('TV Mode')).toBeVisible({ timeout: 10_000 });
  });

  test('shows either KPIs or empty state', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(2000);
    const body = page.locator('body');
    // Either fresh install empty state or real KPI cards
    const hasKPIs = await body.getByText('Registered Agents').isVisible().catch(() => false);
    const hasEmpty = await body.getByText('No activity yet').isVisible().catch(() => false);
    expect(hasKPIs || hasEmpty).toBeTruthy();
  });
});
