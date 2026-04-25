import { test, expect } from '@playwright/test';

/**
 * Sealed-i — Task 30: E2E specs for the SealedCascadePreview component.
 *
 * SealedCascadePreview is embedded in two surfaces:
 *   1. Project Settings (`/settings/projects`) — shows the system-default
 *      security profile card per project.
 *   2. Workflow sealed page (`/sealed/workflows`) — shows the cascade
 *      resolver output (system → project → workflow).
 *
 * Both pages render their headings without requiring auth fixtures or
 * a live backend cascade resolution, so we can assert static mount.
 *
 * Dynamic assertions (cascade resolution order, override picker, workflow
 * profile assignment) require admin auth + seeded data and are gated
 * until Plan 3b-ii adds the test-fixture API.
 */
test.describe('Sealed cascade preview', () => {
  test('workflows page mounts cleanly', async ({ page }) => {
    await page.goto('/sealed/workflows');
    await page.waitForTimeout(2000);

    // The page must load without errors
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');

    // The Workflow Secrets heading (or sealed workflows heading) must be visible
    await expect(page.getByRole('heading', { name: /workflow.*secret|sealed.*workflow/i })).toBeVisible();
  });

  test('cascade resolver shows system → project → workflow order', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded cascade data; gated until Plan 3b-ii lands fixture API',
    );
  });

  test('project override picker changes cascade preview in real time', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profiles; gated until Plan 3b-ii',
    );
  });

  test('workflow-level profile assignment persists across reload', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded workflow with profile assignment; gated until Plan 3b-ii',
    );
  });
});
