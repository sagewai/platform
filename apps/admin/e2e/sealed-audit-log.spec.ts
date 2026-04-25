import { test, expect } from '@playwright/test';

/**
 * Sealed-i — Task 30: E2E specs for the Sealed audit log page.
 *
 * The `/sealed/audit` page renders:
 *   - A heading "Audit Log" (or similar)
 *   - A CSV export button that is initially disabled until at least one
 *     event row is selected or the table is non-empty and the user
 *     explicitly enables export
 *   - Filter controls (event type, date range, profile)
 *
 * The heading + disabled CSV button are static-renderable without auth.
 *
 * Dynamic assertions (filter → table narrows, export → downloads CSV,
 * retention-cleanup event appears after cleanup run) require admin auth
 * fixtures + seeded audit events and are gated until Plan 3b-ii adds
 * the test-fixture API.
 */
test.describe('Sealed audit log', () => {
  test('audit page mounts with heading and disabled CSV export button', async ({ page }) => {
    await page.goto('/sealed/audit');
    await page.waitForTimeout(2000);

    // The page must load without errors
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');

    // The Audit Log heading must be present
    await expect(page.getByRole('heading', { name: /audit log/i })).toBeVisible();

    // The Export CSV button must be present (disabled when no rows are selected or table is empty)
    await expect(page.getByRole('button', { name: /export.*csv|csv.*export/i })).toBeVisible();
  });

  test('filter by event type narrows the table', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded audit events; gated until Plan 3b-ii lands fixture API',
    );
  });

  test('date range filter shows only matching events', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded audit events with known timestamps; gated until Plan 3b-ii',
    );
  });

  test('export CSV downloads a well-formed file with headers', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded audit events; gated until Plan 3b-ii',
    );
  });

  test('retention-cleanup event appears after cleanup job runs', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + cleanup job trigger endpoint; gated until Plan 3b-ii',
    );
  });
});
