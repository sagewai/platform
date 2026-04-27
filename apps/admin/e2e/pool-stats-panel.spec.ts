import { test, expect } from '@playwright/test';

/**
 * Plan 1.5 — Task 16: E2E specs for the PoolStatsPanel component.
 *
 * The worker detail page (/fleet/workers/{id}) embeds PoolStatsPanel, which
 * polls /api/v1/admin/fleet/workers/{id}/pool-stats every 10s. When the
 * backend returns null (worker registered but no stats yet) the component
 * renders an empty-state message.
 *
 * The static-DOM assertion below navigates to a demo worker ID and checks
 * the page loads without a crash. The panel itself renders an empty-state
 * (no real backend) or the loading skeleton — both are valid outcomes for
 * this smoke.
 *
 * Full data-dependent assertions (panel with real snapshot data) require
 * admin auth fixtures + a seeded worker with reported pool stats and are
 * gated until Plan 3b-ii adds the test-fixture API.
 */
test.describe('PoolStatsPanel', () => {
  test('worker detail page mounts without crash', async ({ page }) => {
    await page.goto('/fleet/workers/w-001');
    await page.waitForTimeout(2000);

    // Page must load without application error
    await expect(page.locator('body')).not.toContainText('Application error');
    await expect(page.locator('body')).not.toContainText('Unhandled Runtime Error');

    // The worker detail page header must be visible (demo data covers w-001)
    await expect(page.getByRole('heading', { name: /gpu-worker-us-east/i })).toBeVisible();
  });

  test('pool stats panel shows empty-state or loading skeleton', async ({ page }) => {
    await page.goto('/fleet/workers/w-001');
    await page.waitForTimeout(3000);

    // Either the empty-state copy or the loading skeleton is present —
    // both are valid without a live backend.
    const panelVisible =
      (await page.locator('text=No pool stats reported yet').count()) > 0 ||
      (await page.locator('text=Sandbox Pool').count()) > 0;
    expect(panelVisible).toBe(true);
  });

  test('panel with live snapshot data from real API', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded worker with pool-stats report; gated until Plan 3b-ii lands fixture API',
    );
  });
});
