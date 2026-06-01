import { test, expect } from '@playwright/test';

/**
 * End-to-end tests for the autopilot mission lifecycle.
 *
 * These tests require the backend to be running with autopilot enabled and a
 * configured tier.  In CI they run against the dev server started by
 * playwright.config.ts.  For fast iteration the backend can be run with
 * AUTOPILOT_FAKE_DRIVER=1 (agree with Plan H's owner on the env flag) to
 * complete missions in seconds rather than waiting for real LLM calls.
 *
 * The tests are marked with { tag: '@autopilot' } so they can be run
 * separately from the main e2e suite until Plan H lands the run endpoint.
 */

test.describe('autopilot nav tabs', () => {
  test('Goals tab is active on /autopilot', async ({ page }) => {
    await page.goto('/autopilot');
    const goalsTab = page.getByRole('tab', { name: 'Goals' });
    await expect(goalsTab).toBeVisible();
    await expect(goalsTab).toHaveAttribute('aria-selected', 'true');
  });

  test('nav tabs navigate between autopilot pages', async ({ page }) => {
    await page.goto('/autopilot');

    await page.getByRole('tab', { name: 'Missions' }).click();
    await expect(page).toHaveURL(/\/autopilot\/missions$/);

    await page.getByRole('tab', { name: 'Orchestration' }).click();
    await expect(page).toHaveURL(/\/autopilot\/orchestration$/);

    await page.getByRole('tab', { name: 'Goals' }).click();
    await expect(page).toHaveURL(/\/autopilot$/);
  });

  test('/autopilot/missions renders missions table', async ({ page }) => {
    await page.goto('/autopilot/missions');
    // The page should render without error regardless of whether there are missions.
    // exact: true so we match the page <h1>Missions</h1> only — without it the
    // substring match also hits the empty-state <h2>No missions yet.</h2>, which
    // is always present here (this spec hits the real empty backend), tripping a
    // strict-mode violation.
    await expect(page.getByRole('heading', { name: 'Missions', exact: true })).toBeVisible();
  });
});

/**
 * Full lifecycle test — requires AUTOPILOT_FAKE_DRIVER=1 in backend env.
 * Skipped until Plan H ships the run endpoint.
 */
test.skip('mission status badge transitions live via SSE', async ({ page }) => {
  await page.goto('/autopilot');
  await page.getByRole('textbox', { name: /goal/i }).fill('triage support tickets');
  await page.getByRole('button', { name: /submit/i }).click();
  await page.getByRole('button', { name: /approve/i }).click();

  await page.goto('/autopilot/missions');
  const row = page.getByRole('row', { name: /triage support tickets/i });
  await expect(row.getByText('PENDING')).toBeVisible();

  await page.getByRole('button', { name: /run/i }).first().click();
  await expect(row.getByText('RUNNING')).toBeVisible({ timeout: 5_000 });
  await expect(row.getByText('COMPLETED')).toBeVisible({ timeout: 30_000 });

  // No reload happened
  await expect(page.url()).toContain('/autopilot/missions');
});
