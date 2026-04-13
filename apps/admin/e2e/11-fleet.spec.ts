import { test, expect } from '@playwright/test';

test.describe('Fleet', () => {
  test('/fleet loads worker list', async ({ page }) => {
    await page.goto('/fleet');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('/fleet/enrollment-keys loads', async ({ page }) => {
    await page.goto('/fleet/enrollment-keys');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });
});
