import { test, expect } from '@playwright/test';

/**
 * Plan ART — Task 13: E2E specs for the ArtifactDestinationForm component.
 *
 * Mounted on `/sealed/workflows` after the workflow Sealed config form.
 * The card depends on a workflow-name input + the Sealed cascade preview's
 * effective_secret_keys to populate its env-keys multi-select.
 *
 * The static-mount check runs without auth (matches sealed-cascade-preview
 * spec). Dynamic assertions (PUT/DELETE round-trip, env-keys multi-select
 * populated by a real cascade) require admin auth fixtures + seeded
 * profiles; gated until Plan 3b-ii ships the test-fixture API.
 */
test.describe('Artifact destination card', () => {
  test('workflows page renders without errors', async ({ page }) => {
    await page.goto('/sealed/workflows');
    await page.waitForTimeout(2000);

    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
    await expect(
      page.getByRole('heading', { name: /workflow.*secret|sealed.*workflow/i }),
    ).toBeVisible();
  });

  test('artifact destination card mounts after workflow load', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + a loaded workflow; gated until Plan 3b-ii',
    );
  });

  test('save then reload round-trips the destination', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded workflow + cascade; gated until Plan 3b-ii',
    );
  });

  test('clear admin override returns the card to default state', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded workflow override; gated until Plan 3b-ii',
    );
  });

  test('env-keys multi-select offers only effective_secret_keys from cascade', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded Sealed profile with secret_keys; gated until Plan 3b-ii',
    );
  });
});
