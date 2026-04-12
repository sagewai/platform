import { test, expect } from '@playwright/test';

/**
 * Setup wizard tests verify that the first-time setup experience
 * works correctly. The global-setup.ts already ran the setup wizard
 * via API, so setup_required is false. These tests verify the UI
 * renders correctly by checking the setup completion page.
 *
 * For a full wizard walkthrough (from fresh state), run:
 *   rm ~/.sagewai/admin-state.json
 *   just admin-e2e -- e2e/01-setup-wizard.spec.ts
 */
test.describe('Setup Wizard', () => {
  test('setup is complete — redirects away from /setup', async ({ page }) => {
    // After global-setup, setup_required is false → /setup redirects to /
    await page.goto('/setup');
    await expect(page).not.toHaveURL(/\/setup/);
  });

  test('/login is accessible after setup', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByText('Sign in to your account')).toBeVisible();
  });
});
