import { test, expect } from '@playwright/test';

test.describe('Safety', () => {
  test('/safety/guardrails loads', async ({ page }) => {
    await page.goto('/safety/guardrails');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('/safety/audit loads', async ({ page }) => {
    await page.goto('/safety/audit');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });
});
