import { test, expect } from '@playwright/test';

test.describe('System Settings', () => {
  test('/system/organization loads org settings', async ({ page }) => {
    await page.goto('/system/organization');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('/system/models loads provider config page', async ({ page }) => {
    await page.goto('/system/models');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('/settings/projects loads project list', async ({ page }) => {
    await page.goto('/settings/projects');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });
});
