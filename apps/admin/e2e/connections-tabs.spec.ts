import { test, expect } from '@playwright/test';

test('connections page renders Inference and Tools tabs', async ({ page }) => {
  await page.goto('/connections');
  await expect(page.getByRole('tab', { name: /inference/i })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole('tab', { name: /tools/i })).toBeVisible();
  await page.getByRole('tab', { name: /tools/i }).click();
  await expect(page.getByText(/no tool connections yet/i)).toBeVisible();
});
