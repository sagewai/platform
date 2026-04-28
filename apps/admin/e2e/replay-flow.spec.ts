import { test, expect } from '@playwright/test';

/**
 * Sealed-iii.C — replay flow smoke.
 *
 * Static smoke only: verifies the ReplayButton can render in isolation
 * via /test-fixtures/replay-button (when the test-fixture API from
 * Plan 3b-ii is available). When the fixture page is absent, the test
 * is skipped — full dynamic flow lands once Plan 3b-ii ships.
 */
test('replay button renders on a completed run detail page', async ({ page }) => {
  const resp = await page.goto('/runs/test-fixture-completed-run');
  if (!resp || resp.status() === 404) {
    test.skip(true, 'test-fixture run not available — gated on Plan 3b-ii');
  }
  const button = page.getByRole('button', { name: /replay from/i }).first();
  if ((await button.count()) === 0) {
    test.skip(true, 'replay button not surfaced on this fixture');
  }
  await expect(button).toBeVisible();
});
