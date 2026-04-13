import { test, expect } from '@playwright/test';

test.describe('Playground', () => {
  test('/playground loads without crashing', async ({ page }) => {
    await page.goto('/playground');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });
});
