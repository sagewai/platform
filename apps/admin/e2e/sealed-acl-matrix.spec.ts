import { test, expect } from '@playwright/test';

/**
 * Sealed-iii — Task 20: E2E smoke test for the AclMatrix component.
 *
 * The AclMatrix component renders on the profile detail page at
 * `/sealed/profiles/[id]` under a "Per-CLI access" heading. It displays
 * a matrix of CLI versions × access levels.
 *
 * This smoke test:
 *   1. Navigates to `/sealed/profiles` (profiles list)
 *   2. Clicks the first profile (if any exist)
 *   3. Asserts the "Per-CLI access" heading is visible
 *
 * Dynamic editing of the matrix is gated until Plan 3b-ii adds the
 * test-fixture API for profile seeding. The test auto-skips if no
 * profiles are seeded in the environment.
 */
test.describe('Sealed ACL matrix', () => {
  test('renders on profile detail page', async ({ page }) => {
    await page.goto('/sealed/profiles');
    await page.waitForTimeout(2000);

    // The page must load without errors
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');

    // Find the first real profile detail link (/sealed/profiles/<id>). Scoping
    // by href avoids matching the "New Profile" action (/sealed/profiles/new)
    // or unrelated nav links such as the sidebar's account "Profile & Password".
    const firstProfileLink = page
      .locator('a[href^="/sealed/profiles/"]:not([href="/sealed/profiles/new"])')
      .first();

    if ((await firstProfileLink.count()) === 0) {
      test.skip(true, 'no profiles seeded in this environment; fixture API gated on Plan 3b-ii');
    }

    await firstProfileLink.click();
    await page.waitForTimeout(1000);

    // The Per-CLI access heading must be visible on the detail page
    await expect(page.getByRole('heading', { name: /per-cli access/i })).toBeVisible();
  });

  test('matrix editing requires authenticated user', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded profile with secrets; gated until Plan 3b-ii',
    );
  });
});
