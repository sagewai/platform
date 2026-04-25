import { test, expect } from '@playwright/test';

/**
 * Sealed-i — Task 30: E2E specs for the RevealButton flow.
 *
 * The RevealButton component lives on the profile detail page
 * `/sealed/profiles/[id]`. In production it:
 *   1. Shows a masked placeholder for each secret value.
 *   2. On click opens a confirmation dialog.
 *   3. On confirm calls POST /api/v1/admin/sealed/profiles/:id/reveal.
 *   4. Writes a `secret.decrypted` audit event.
 *   5. Auto-masks the secret after 10 s.
 *
 * All dynamic assertions require a seeded profile with at least one
 * secret entry and a logged-in admin session. These are gated until
 * Plan 3b-ii adds the test-fixture API.
 *
 * The one static assertion navigates to the profiles list and verifies
 * the page mounts cleanly — the detail page cannot be visited without
 * a real profile ID.
 */
test.describe('Sealed reveal flow', () => {
  test('profiles list mounts cleanly (prerequisite smoke)', async ({ page }) => {
    await page.goto('/sealed/profiles');
    await page.waitForTimeout(2000);

    // The page must load without errors
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');

    // Heading must be visible
    await expect(page.getByRole('heading', { name: /security profiles/i })).toBeVisible();
  });

  test('reveal button shows masked placeholder by default', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile; gated until Plan 3b-ii lands fixture API',
    );
  });

  test('click reveal → confirmation dialog appears', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile; gated until Plan 3b-ii',
    );
  });

  test('confirm reveal → secret value visible for 10 s then auto-masked', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile with secret; gated until Plan 3b-ii',
    );
  });

  test('reveal emits secret.decrypted audit event', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile + audit log endpoint; gated until Plan 3b-ii',
    );
  });
});
