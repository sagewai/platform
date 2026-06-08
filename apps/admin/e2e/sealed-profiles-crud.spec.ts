import { test, expect } from '@playwright/test';

/**
 * Sealed-i — Task 30: E2E specs for the Security Profiles CRUD surface.
 *
 * The `/sealed/profiles` page renders a profile list with a heading
 * "Security Profiles" and a "+ New Profile" action button. Both are
 * present in the static DOM immediately after mount without requiring
 * backend auth fixtures.
 *
 * Data-dependent assertions (create → appears in list, edit → saved,
 * delete → removed, reveal → masked after 10 s) require admin auth
 * fixtures + a seeded profile and are gated until Plan 3b-ii adds the
 * test-fixture API.
 */
test.describe('Sealed profiles CRUD', () => {
  test('list page mounts with heading and New Profile button', async ({ page }) => {
    await page.goto('/sealed/profiles');
    await page.waitForTimeout(2000);

    // The page must load without errors
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');

    // The Security Profiles heading must be present
    await expect(page.getByRole('heading', { name: /security profiles/i })).toBeVisible();

    // The New Profile action must be present. It is a link styled as a button
    // (it navigates to /sealed/profiles/new), so match it by the link role.
    await expect(page.getByRole('link', { name: /new profile/i })).toBeVisible();
  });

  test('create profile → appears in list', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + fixture API; gated until Plan 3b-ii lands fixture API',
    );
  });

  test('edit profile → saved values reload on next visit', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile; gated until Plan 3b-ii',
    );
  });

  test('delete profile → removed from list', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile; gated until Plan 3b-ii',
    );
  });

  test('profile detail page renders secret entries', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile with secrets; gated until Plan 3b-ii',
    );
  });
});
