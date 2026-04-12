import { test, expect } from '@playwright/test';
import { authenticate } from './mock-api';

test.describe('Playground', () => {
  test.beforeEach(async ({ page }) => {
    await authenticate(page);
  });

  test('/playground loads without crashing', async ({ page }) => {
    await page.goto('/playground');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });
});
