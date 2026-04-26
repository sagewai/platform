import { test, expect } from '@playwright/test';

/**
 * Sealed-iii.A — Task 23: E2E specs for the Revocations page.
 *
 * The `/sealed/revocations` page renders the active revocation list with
 * a heading "Sealed Revocations". This is verifiable from the static DOM
 * immediately after mount without requiring backend auth fixtures.
 *
 * Data-dependent assertions (revoke from profile detail, hard-revoke confirm
 * modal, lift from revocations page) require admin auth fixtures + a seeded
 * profile and are gated until Plan 3b-ii adds the test-fixture API.
 */
test.describe('Sealed revocations', () => {
  test('revocations list page mounts with empty state', async ({ page }) => {
    await page.goto('/sealed/revocations');
    await expect(page.getByRole('heading', { name: /sealed revocations/i })).toBeVisible();
  });

  test('revoke from profile detail page', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile; gated until Plan 3b-ii lands fixture API',
    );
  });

  test('hard-revoke confirm modal shows affected runs', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile; gated until Plan 3b-ii lands fixture API',
    );
  });

  test('lift from revocations page', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile; gated until Plan 3b-ii lands fixture API',
    );
  });
});
