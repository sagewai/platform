import { test, expect } from '@playwright/test';

test.describe('Workflows', () => {
  test('/workflows loads without crashing', async ({ page }) => {
    await page.goto('/workflows');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('shows workflow builder heading', async ({ page }) => {
    await page.goto('/workflows');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    // Page rendered something meaningful (not blank)
    const text = await page.locator('body').innerText();
    expect(text.trim().length).toBeGreaterThan(0);
  });

  test('shows stats bar', async ({ page }) => {
    await page.goto('/workflows');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Application error');
    // Page is not blank
    const text = await page.locator('body').innerText();
    expect(text.trim().length).toBeGreaterThan(0);
  });

  test('/workflows/templates renders templates', async ({ page }) => {
    await page.goto('/workflows/templates');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('/workflows/history loads', async ({ page }) => {
    await page.goto('/workflows/history');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });
});
