import { test, expect } from '@playwright/test';

/**
 * Plan 3b-i — Task 15: E2E specs for the Sandbox project defaults card.
 *
 * The Project Settings page (/settings/projects) renders a
 * <ProjectSandboxDefaultsCard> inside each project accordion body. The
 * card heading "Sandbox defaults" (h3) is rendered as soon as any
 * accordion item is expanded, so we can assert its presence without
 * auth fixtures or seeded overrides.
 *
 * Data-dependent assertions (save → reload retains values, clear button
 * reverts override) require admin auth fixtures + a seeded project
 * override and are gated until Plan 3b-ii adds the test-fixture API.
 */
test.describe('Sandbox project defaults form', () => {
  test('renders Sandbox defaults card on Project Settings', async ({ page }) => {
    await page.goto('/settings/projects');
    await page.waitForTimeout(2000);

    // The page must load without errors
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');

    // The card heading is rendered inside each expanded accordion body.
    // At minimum one project accordion is expanded by default (or the user
    // clicks to expand), but the heading is present in the DOM once any
    // ProjectSandboxDefaultsCard mounts.
    await expect(page.getByText(/Sandbox defaults/i).first()).toBeVisible();
  });

  test('save → reload retains values', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + project seed; gated until Plan 3b-ii lands fixture API',
    );
  });

  test('clear button reverts override', async ({ page }) => {
    test.skip(
      true,
      'Requires admin auth fixture + seeded override; gated until Plan 3b-ii',
    );
  });
});
