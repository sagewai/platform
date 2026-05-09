// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import { test, expect } from '@playwright/test';

/**
 * Plan O — Branded empty, loading, and error states.
 *
 * Tests verify the correct component renders in each scenario by
 * checking `data-testid` attributes. No backend needed.
 */

test.describe('MissionDetailLoadingState', () => {
  test('shows graph skeleton while loading', async ({ page }) => {
    // Delay the mission response so the loading state is visible.
    await page.route('**/api/v1/autopilot/missions/slow-mission', async (route) => {
      await new Promise((r) => setTimeout(r, 3000));
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'slow-mission', status: 'pending' }),
      });
    });
    await page.goto('/autopilot/missions/slow-mission');
    await expect(page.getByTestId('mission-detail-loading')).toBeVisible({ timeout: 2000 });
    await expect(page.getByTestId('agent-graph-skeleton')).toBeVisible();
  });
});

test.describe('NotFoundMission', () => {
  test('branded 404 with back-to-missions link', async ({ page }) => {
    await page.route('**/api/v1/autopilot/missions/does-not-exist', (route) =>
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) }),
    );
    await page.goto('/autopilot/missions/does-not-exist');
    const notFound = page.getByTestId('not-found-mission');
    await expect(notFound).toBeVisible({ timeout: 5000 });
    const link = notFound.getByTestId('back-to-missions-link');
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', '/autopilot/missions');
  });
});

test.describe('MissionLoadError', () => {
  test('branded error with retry button', async ({ page }) => {
    let calls = 0;
    await page.route('**/api/v1/autopilot/missions/error-mission', (route) => {
      calls++;
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Internal error' }) });
    });
    await page.goto('/autopilot/missions/error-mission');
    await expect(page.getByTestId('mission-load-error')).toBeVisible({ timeout: 5000 });
    const retryBtn = page.getByTestId('retry-button');
    await expect(retryBtn).toBeVisible();
    // Clicking retry triggers a new API call.
    const prevCalls = calls;
    await retryBtn.click();
    await page.waitForTimeout(500);
    expect(calls).toBeGreaterThan(prevCalls);
  });
});

test.describe('EmptyMissionsPage', () => {
  test('shows empty hero when no missions exist', async ({ page }) => {
    await page.route('**/api/v1/autopilot/missions**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ missions: [], total: 0 }),
      }),
    );
    await page.goto('/autopilot/missions');
    const empty = page.getByTestId('empty-missions-page');
    await expect(empty).toBeVisible({ timeout: 5000 });
    await expect(empty.getByTestId('start-goal-link')).toHaveAttribute('href', '/autopilot');
  });
});

test.describe('OnboardingNudge', () => {
  test('nudge appears and can be dismissed', async ({ page }) => {
    // Clear the dismissal key in localStorage before the test.
    await page.addInitScript(() => {
      localStorage.removeItem('sagewai.autopilot.onboarding.dismissed');
    });
    // Mock the autopilot status so the enabled section (and nudge) renders.
    await page.route('**/api/v1/autopilot/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          enabled: true,
          tier: 'anonymous',
          quota_used: 0,
          quota_limit: 100,
        }),
      }),
    );
    await page.route('**/api/v1/autopilot/missions**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ missions: [], total: 0 }),
      }),
    );
    await page.goto('/autopilot');
    const nudge = page.getByTestId('onboarding-nudge');
    await expect(nudge).toBeVisible({ timeout: 5000 });
    await page.getByTestId('dismiss-nudge').click();
    await expect(nudge).not.toBeVisible({ timeout: 2000 });
  });

  test('nudge stays hidden after dismissal', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('sagewai.autopilot.onboarding.dismissed', '1');
    });
    await page.route('**/api/v1/autopilot/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          enabled: true,
          tier: 'anonymous',
          quota_used: 0,
          quota_limit: 100,
        }),
      }),
    );
    await page.route('**/api/v1/autopilot/missions**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ missions: [], total: 0 }),
      }),
    );
    await page.goto('/autopilot');
    await page.waitForTimeout(800);
    await expect(page.getByTestId('onboarding-nudge')).not.toBeVisible();
  });
});

test.describe('EmptyAutopilotPage', () => {
  test('sample-goal pills prefill goal input', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.removeItem('sagewai.autopilot.onboarding.dismissed');
    });
    await page.route('**/api/v1/autopilot/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          enabled: true,
          tier: 'anonymous',
          quota_used: 0,
          quota_limit: 100,
        }),
      }),
    );
    await page.route('**/api/v1/autopilot/missions**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ missions: [], total: 0 }),
      }),
    );
    await page.goto('/autopilot');
    const emptyPage = page.getByTestId('empty-autopilot-page');
    await expect(emptyPage).toBeVisible({ timeout: 5000 });
    const pills = emptyPage.getByTestId('sample-goal-pill');
    await expect(pills).toHaveCount(3);
    const firstPill = pills.first();
    const goalText = await firstPill.textContent();
    await firstPill.click();
    // The goal input should now contain the pill text.
    const input = page.getByPlaceholder(/describe your goal/i);
    await expect(input).toHaveValue(goalText?.trim() ?? '');
  });
});

test.describe('AgentGraphSkeleton', () => {
  test('has correct role and aria-label', async ({ page }) => {
    await page.route('**/api/v1/autopilot/missions/skeleton-test', async (route) => {
      await new Promise((r) => setTimeout(r, 3000));
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    });
    await page.goto('/autopilot/missions/skeleton-test');
    const skeleton = page.getByTestId('agent-graph-skeleton');
    await expect(skeleton).toBeVisible({ timeout: 2000 });
    await expect(skeleton).toHaveAttribute('role', 'status');
    await expect(skeleton).toHaveAttribute('aria-label', 'Loading mission graph');
  });
});
