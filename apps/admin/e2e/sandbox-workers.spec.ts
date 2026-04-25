import { test, expect } from '@playwright/test';

/**
 * Plan 3a — Task 19: Admin surface tests for sandbox routing labels.
 *
 * The Workers page (/fleet) renders demo data that already includes
 * sandbox labels on worker w-001 (`sandbox.mode=per_run`). The column
 * header and badge assertions below are therefore stable against the
 * static DEMO_WORKERS fixture and do not require a live backend seeding
 * step.
 *
 * Data-dependent assertions (specific mode badges, per-worker label
 * values from a real API response) are marked as skipped pending a
 * test-fixture API endpoint — see Plan 3b.
 */
test.describe('Sandbox routing admin surface', () => {
  test('shows Sandbox column header on Fleet Workers page', async ({ page }) => {
    await page.goto('/fleet');
    await page.waitForTimeout(2000);

    // The page must load without errors
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');

    // The Sandbox column header must be present in the workers table
    await expect(page.getByRole('columnheader', { name: /Sandbox/i })).toBeVisible();
  });

  test('renders mode tag for a sandboxed worker (demo data)', async ({ page }) => {
    await page.goto('/fleet');
    await page.waitForTimeout(2000);

    await expect(page.locator('body')).not.toContainText('Backend not reachable');

    // Demo worker w-001 has sandbox.mode=per_run — its mode badge should appear
    // The SandboxSummary component renders the mode value as visible text
    await expect(page.locator('text=per_run').first()).toBeVisible();
  });

  test('renders Unsandboxed badge for workers without sandbox labels (demo data)', async ({
    page,
  }) => {
    await page.goto('/fleet');
    await page.waitForTimeout(2000);

    await expect(page.locator('body')).not.toContainText('Backend not reachable');

    // Workers w-002 through w-005 have empty labels → UnsandboxedBadge renders
    await expect(page.locator('text=⚠ Unsandboxed').first()).toBeVisible();
  });

  test('renders mode badge and detail popover based on API-seeded labels', async ({ page }) => {
    // NOTE: This test requires a /api/test-fixtures/workers endpoint (or equivalent)
    // to seed a worker with known sandbox labels and assert precise badge values.
    // No such endpoint exists in Plan 3a. Skipped here; Plan 3b adds fixture support.
    test.skip(
      true,
      'Requires test-fixture API or mock-data injection — see Plan 3a Task 19 notes.',
    );
  });
});
