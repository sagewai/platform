import { test, expect } from '@playwright/test';

test.describe('Analytics', () => {
  test('/analytics/costs loads', async ({ page }) => {
    await page.goto('/analytics/costs');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('/analytics/models loads', async ({ page }) => {
    await page.goto('/analytics/models');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });
});
