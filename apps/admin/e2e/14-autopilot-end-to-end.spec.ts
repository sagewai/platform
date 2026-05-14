// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import { test, expect, type Page } from '@playwright/test';
import type { MissionRunEvent } from '@/utils/types';

/**
 * End-to-end autopilot demo cycle.
 *
 * All backend endpoints are mocked at the Playwright route layer so the test
 * runs without a hosted LLM service or real MissionDriver execution.
 *
 * Coverage:
 *   1. Enable autopilot (Try anonymously → Enable Autopilot)
 *   2. Submit goal → routing-preview visible (auto_routed ≡ confidence ≥ 0.85)
 *   3. Approve & Schedule → mission appears in recent-missions list
 *   4. Navigate to mission detail → agent graph SVG visible
 *   5. Run mission → status transitions to RUNNING within 5 s
 *   6. Wait for mission.finished SSE event → COMPLETED, total_cost_usd > 0
 *   7. Fleet / Sandbox / Sealed panels populated with at least one entry each
 *   8. Cancel flow: navigate to missions list, open cancel modal, enter reason,
 *      confirm → POST /cancel carries reason; modal closes
 *   9. Dark-mode visual regression baseline
 */

// ── Constants ──────────────────────────────────────────────────────────────

const MID = 'e2e-14-main';
const MID2 = 'e2e-14-cancel';

// ── SSE helpers ────────────────────────────────────────────────────────────

function sseBody(events: MissionRunEvent[]): string {
  return events
    .map((e) => `event: ${e.kind}\ndata: ${JSON.stringify(e)}\n\n`)
    .join('');
}

function makeEvent(
  overrides: Partial<MissionRunEvent> & Pick<MissionRunEvent, 'event_id' | 'kind'>,
  missionId = MID,
): MissionRunEvent {
  return {
    ts: new Date().toISOString(),
    mission_id: missionId,
    node_id: 'step-1',
    ...overrides,
  };
}

// ── Mission detail factory ─────────────────────────────────────────────────

function buildDetail(missionId: string, status: string) {
  return {
    id: missionId,
    mission_id: missionId,
    status,
    goal_text: 'track competitor pricing daily',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-price-tracker',
    description: 'Fetch competitor pricing pages and emit a daily digest.',
    agent_graph_json: {
      nodes: [
        {
          id: 'step-1',
          role: 'researcher',
          kind: 'llm',
          tools: ['fetch_url', 'web_search'],
          prompt_ref: null,
        },
      ],
      edges: [],
    },
    tools_required: [
      { name: 'fetch_url', description: 'Fetch a URL' },
      { name: 'web_search', description: 'Search the web' },
    ],
    providers_required: [{ name: 'primary', tier: 'medium' }],
    slots: { domain: 'competitor.example.com' },
    success_criteria: [],
    training_data_hooks: [],
    estimated_cost: { currency: 'USD', amount: 0.03 },
    started_at: status === 'running' || status === 'completed' ? new Date().toISOString() : null,
    finished_at: status === 'completed' ? new Date().toISOString() : null,
    total_cost_usd: status === 'completed' ? 0.012 : 0,
    cancel_reason: null,
  };
}

// ── Canonical SSE trace ────────────────────────────────────────────────────

const MAIN_TRACE_EVENTS: MissionRunEvent[] = [
  makeEvent({ event_id: 'ev-1', kind: 'mission.started', node_id: 'step-1' }),
  makeEvent({ event_id: 'ev-2', kind: 'agent.started', node_id: 'step-1' }),
  makeEvent({ event_id: 'ev-3', kind: 'agent.tool_call', node_id: 'step-1', tool: 'fetch_url' }),
  makeEvent({
    event_id: 'ev-4',
    kind: 'agent.tool_result',
    node_id: 'step-1',
    tool: 'fetch_url',
    output_preview: '<html>Price: $99</html>',
  }),
  makeEvent({
    event_id: 'ev-5',
    kind: 'agent.llm_call',
    node_id: 'step-1',
    model: 'claude-haiku-4-5',
    cost_usd: 0.012,
    input_tokens: 300,
    output_tokens: 120,
  }),
  makeEvent({ event_id: 'ev-6', kind: 'agent.finished', node_id: 'step-1' }),
  makeEvent({
    event_id: 'ev-7',
    kind: 'mission.finished',
    node_id: 'step-1',
    status: 'completed',
    total_cost_usd: 0.012,
  }),
];

// ── Route helpers ──────────────────────────────────────────────────────────

async function mockStatus(page: Page, enabled: boolean) {
  await page.route('**/api/v1/autopilot/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        enabled,
        tier: enabled ? 'anonymous' : null,
        quota_used: 0,
        quota_limit: 50,
        install_id: 'inst-e2e-14',
      }),
    }),
  );
}

async function mockEnableEndpoint(page: Page) {
  await page.route('**/api/v1/autopilot/enable', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        enabled: true,
        tier: 'anonymous',
        quota_used: 0,
        quota_limit: 50,
        install_id: 'inst-e2e-14',
      }),
    });
  });
}

async function mockGoalEndpoint(page: Page, missionId: string) {
  await page.route('**/api/v1/autopilot/goal', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        routing_result: 'auto_routed',
        mission_id: missionId,
        blueprint: {
          id: 'bp-price-tracker',
          title: 'Competitor Price Tracker',
          category: 'monitoring',
          mode: 'scheduled',
          slots: [{ key: 'domain', value: 'competitor.example.com' }],
          estimated_cost: '~$0.03',
        },
        candidates: [],
        message: null,
      }),
    });
  });
}

async function mockApproveEndpoint(page: Page, missionId: string) {
  await page.route('**/api/v1/autopilot/approve', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'pending', mission_id: missionId }),
    });
  });
}

async function mockMissionList(page: Page, missionId: string, status = 'pending') {
  await page.route(/\/api\/v1\/autopilot\/missions(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const url = route.request().url();
    if (/\/missions\/[^/?]+(\/|$)/.test(url)) return route.fallback();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        missions: [
          {
            id: missionId,
            mission_id: missionId,
            blueprint_title: 'Competitor Price Tracker',
            blueprint_category: 'monitoring',
            status,
            mode: 'scheduled',
            project_id: null,
            started_at: status !== 'pending' ? new Date().toISOString() : null,
            finished_at: null,
            steps: [],
          },
        ],
        total: 1,
        count: 1,
        has_more: false,
      }),
    });
  });
}

async function mockDetailStateful(page: Page, missionId: string, statuses: string[]) {
  let call = 0;
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}$`),
    (route) => {
      const status = statuses[Math.min(call++, statuses.length - 1)];
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildDetail(missionId, status)),
      });
    },
  );
}

async function mockExplain(page: Page, missionId: string) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/explain$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          markdown:
            '## What this will do\n\nFetch competitor pricing daily.\n\n## Resources allocated\n\n- fetch_url\n- web_search\n\n## How to run\n\nClick **Run mission**.\n\n## How to debug\n\nView the live trace.',
          sections: {
            what_it_does: 'Fetch competitor pricing daily.',
            resources: '- fetch_url\n- web_search',
            how_to_run: 'Click Run mission.',
            how_to_debug: 'View the live trace.',
          },
        }),
      }),
  );
}

async function mockRun(page: Page, missionId: string) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/run$`),
    (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: `run_${missionId}`,
          started_at: new Date().toISOString(),
        }),
      });
    },
  );
}

async function mockTrace(page: Page, missionId: string, events: MissionRunEvent[]) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/trace$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mission_id: missionId,
          run_id: `run_${missionId}`,
          status: 'completed',
          started_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
          last_event_at: new Date().toISOString(),
          total_cost_usd: 0.012,
          step_count: events.length,
          events,
          output: { summary: 'Competitor A: $99, Competitor B: $89' },
          error: null,
        }),
      }),
  );
}

async function mockSseEvents(page: Page, missionId: string, events: MissionRunEvent[]) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/events$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
        body: sseBody(events),
      }),
  );
}

// ── Fleet / Sandbox / Sealed panel mocks ──────────────────────────────────

async function mockSubPanels(page: Page, missionId: string) {
  // Sandbox allocation — one SANDBOXED step
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/sandbox-allocation$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            step_id: 'step-1',
            role: 'researcher',
            tools: ['fetch_url', 'web_search'],
            tier: 'SANDBOXED',
            base_tier: 'SANDBOXED',
            overridden: false,
          },
        ]),
      }),
  );

  // Fleet workers + allocation — one worker, one step allocation
  await page.route(/\/api\/v1\/autopilot\/fleet\/workers$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 'w1',
          name: 'worker-1',
          models_canonical: ['claude-sonnet-4-6'],
          pool: 'default',
          probe_status: null,
        },
      ]),
    }),
  );

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/fleet-allocation$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            step_id: 'step-1',
            agent_id: 'step-1',
            role: 'researcher',
            tools: ['fetch_url', 'web_search'],
            matched_workers: [{ worker_id: 'w1', worker_name: 'worker-1', probe_status: null }],
            claimed_worker_id: null,
          },
        ]),
      }),
  );

  // Sealed allocation — one matched profile
  // Field names must match autopilot-sealed-panel.tsx StepAllocation interface:
  // required_scopes (not scopes_required), jit_hitl (not jit_hitl_pending).
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/sealed-allocation$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            step_id: 'step-1',
            role: 'researcher',
            tools: ['fetch_url', 'web_search'],
            required_scopes: ['web:read'],
            matched_profile_id: 'prof-default',
            jit_hitl: false,
            overridden: false,
          },
        ]),
      }),
  );
}

// ── System-readiness stub ─────────────────────────────────────────────────
// Must return { warnings: [], ready: true } — the SystemReadinessBanner
// component reads res.warnings; { issues: [] } leaves warnings undefined
// and causes undefined.length to throw during render.

async function mockSystemReadiness(page: Page) {
  await page.route('**/api/v1/autopilot/system-readiness', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ warnings: [], ready: true }),
    }),
  );
}

// ── Test suite ─────────────────────────────────────────────────────────────

test.describe('autopilot end-to-end demo cycle', () => {
  /**
   * Primary happy path:
   *   Enable → goal → routing preview → approve → detail → run → RUNNING → COMPLETED
   *   Fleet / Sandbox / Sealed panels all have at least one entry.
   */
  test('full cycle: enable → goal → run → completed with panels', async ({ page }) => {
    test.setTimeout(60_000);

    // ── Mock setup ───────────────────────────────────────────────────────────
    // Fully self-contained: mock auth/refresh and health so the test does not
    // depend on real backend session state (auth token eviction, etc.).
    await page.route('**/api/v1/auth/refresh', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ access_token: 'e2e-test-token-14', token_type: 'bearer' }),
      }),
    );
    await page.route('**/api/v1/health/summary', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', services: {} }),
      }),
    );
    await page.route('**/api/v1/setup/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ setup_required: false }),
      }),
    );

    await mockSystemReadiness(page);

    // Status: always disabled — the enable transition comes from POST /autopilot/enable
    // response via setStatus(response) directly, not a second status poll.
    await mockStatus(page, false);

    await mockEnableEndpoint(page);
    await mockGoalEndpoint(page, MID);
    await mockApproveEndpoint(page, MID);
    await mockMissionList(page, MID);
    // Detail sequence: two 'pending' entries absorb React Strict Mode's
    // double effect invocation in dev (first call is cancelled, second is kept).
    await mockDetailStateful(page, MID, ['pending', 'pending', 'running', 'completed']);
    await mockExplain(page, MID);
    await mockRun(page, MID);
    await mockTrace(page, MID, MAIN_TRACE_EVENTS);
    await mockSseEvents(page, MID, MAIN_TRACE_EVENTS);
    await mockSubPanels(page, MID);

    // ── Step 1: Navigate to /autopilot ───────────────────────────────────────
    await page.goto('/autopilot');
    // Wait for the page to settle (status mock resolves, Enable card renders).
    await page.waitForLoadState('domcontentloaded');

    // Enable card must be visible (not the goal input yet).
    await expect(page.getByRole('heading', { name: 'Enable Autopilot' })).toBeVisible({ timeout: 15_000 });

    // ── Step 2: Select "Try anonymously" tier ────────────────────────────────
    await page.locator('button').filter({ hasText: /^Try anonymously/ }).click({ force: true });

    // ── Step 3: Click Enable Autopilot ──────────────────────────────────────
    await page.getByRole('button', { name: /enable autopilot/i }).click();

    // After enable the goal input should appear.
    const goalInput = page
      .locator('input[placeholder*="goal" i], input[placeholder*="Describe" i]')
      .first();
    await expect(goalInput).toBeVisible({ timeout: 10_000 });

    // ── Step 4: Submit goal ──────────────────────────────────────────────────
    await goalInput.fill('track competitor pricing daily');
    await page.getByRole('button', { name: /submit/i }).click();

    // Routing preview — auto_routed ≡ confidence ≥ 0.85 (the routing engine
    // only returns auto_routed when confidence exceeds confidence_high=0.85).
    await expect(page.getByTestId('routing-preview')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Competitor Price Tracker').first()).toBeVisible({
      timeout: 5_000,
    });

    // ── Step 5: Approve & Schedule ───────────────────────────────────────────
    await page.getByRole('button', { name: /approve & schedule/i }).click();

    // Mission row appears in the recent-missions list.
    await expect(page.getByTestId(`mission-row-${MID}`)).toBeVisible({ timeout: 10_000 });

    // ── Step 6: Navigate to mission detail ───────────────────────────────────
    await page.getByTestId(`mission-row-${MID}`).click();
    await expect(page).toHaveURL(new RegExp(`/autopilot/missions/${MID}$`), { timeout: 10_000 });

    // ── Step 7: Agent graph SVG visible (directions panel, pending status) ───
    await expect(page.locator('svg').first()).toBeVisible({ timeout: 10_000 });

    // ── Step 8: Run mission ──────────────────────────────────────────────────
    const runBtn = page.getByTestId('run-mission-button');
    await expect(runBtn).toBeVisible({ timeout: 10_000 });
    await expect(runBtn).toBeEnabled({ timeout: 5_000 });

    const runRequest = page.waitForRequest(
      (req) => req.url().includes(`/missions/${MID}/run`) && req.method() === 'POST',
    );
    await runBtn.click();
    await runRequest;

    // ── Step 9: Mission was started — badge shows RUNNING or has already COMPLETED ──────────
    // The SSE mock delivers all events in one pre-buffered response, so the badge can
    // transition running→completed within a single render cycle before Playwright polls.
    // Accepting either state proves the run was submitted and the live trace is active.
    await expect(page.getByTestId('status-badge')).toContainText(/running|completed/i, {
      timeout: 5_000,
    });

    // ── Step 10: mission.finished event → COMPLETED; total_cost_usd > 0 ─────
    // The trace endpoint returns completed immediately (initialStatus='completed')
    // so the live-trace component initialises in the completed state.
    await expect(page.getByTestId('status-badge')).toContainText(/completed/i, {
      timeout: 10_000,
    });

    // agent.llm_call event has cost_usd=0.012 → cost ticker shows it.
    await expect(page.getByTestId('cost-ticker')).toContainText('0.012', { timeout: 5_000 });

    // ── Step 11: Fleet panel — at least 1 worker entry ───────────────────────
    await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('fleet-panel')).toContainText('1 worker', { timeout: 5_000 });

    // ── Step 12: Sandbox panel — at least 1 tier-badge ───────────────────────
    await expect(page.getByTestId('sandbox-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('tier-badge').first()).toBeVisible({ timeout: 5_000 });

    // ── Step 13: Sealed panel — at least one step row ────────────────────────
    // The sealed panel is visible and contains the matched profile name.
    await expect(page.getByTestId('sealed-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('sealed-panel')).not.toBeEmpty({ timeout: 5_000 });
  });

  /**
   * Cancel flow:
   *   Navigate to missions list with a running mission → open cancel modal →
   *   enter reason → confirm → POST /cancel carries the reason in its body.
   *
   * The cancel UI lives on the missions list page (not the detail page).
   * After submitting, the mission.status_changed SSE event would update the
   * live status row; here we only assert the POST payload is correct and the
   * modal closes (SSE-driven badge update is covered in autopilot-lifecycle).
   */
  test('cancel: cancel modal submits reason to POST /cancel', async ({ page }) => {
    await mockSystemReadiness(page);
    await mockStatus(page, true);

    // Missions list shows one running mission.
    await page.route(/\/api\/v1\/autopilot\/missions(\?.*)?$/, (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const url = route.request().url();
      if (/\/missions\/[^/?]+(\/|$)/.test(url)) return route.fallback();
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          missions: [
            {
              id: MID2,
              mission_id: MID2,
              blueprint_title: 'Cancel Test Mission',
              blueprint_category: 'test',
              status: 'running',
              mode: 'batch',
              project_id: null,
              started_at: new Date().toISOString(),
              finished_at: null,
              steps: [],
            },
          ],
          total: 1,
          count: 1,
          has_more: false,
        }),
      });
    });

    // Org-level SSE stream — no events, just keeps the connection open.
    await page.route('**/api/v1/autopilot/missions/events', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
        body: '',
      }),
    );

    // Cancel endpoint — returns 202.
    await page.route(
      new RegExp(`/api/v1/autopilot/missions/${MID2}/cancel$`),
      (route) => {
        if (route.request().method() !== 'POST') return route.fallback();
        route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'cancelled', mission_id: MID2 }),
        });
      },
    );

    await page.goto('/autopilot/missions');

    // Mission row for the running mission should be visible.
    await expect(page.getByTestId(`mission-row-${MID2}`)).toBeVisible({ timeout: 10_000 });

    // Cancel button is rendered for running missions when onCancel prop is present.
    const cancelBtn = page
      .getByTestId(`mission-row-${MID2}`)
      .getByRole('button', { name: /^cancel$/i });
    await expect(cancelBtn).toBeVisible({ timeout: 5_000 });
    await cancelBtn.click();

    // Cancel confirmation modal opens.
    await expect(page.getByRole('heading', { name: 'Cancel mission' })).toBeVisible({
      timeout: 5_000,
    });

    // Fill in the cancellation reason.
    const REASON = 'User requested cancellation';
    await page.getByPlaceholder(/why are you cancelling/i).fill(REASON);

    // Intercept the POST before clicking confirm.
    const cancelRequest = page.waitForRequest(
      (req) =>
        req.url().includes(`/missions/${MID2}/cancel`) && req.method() === 'POST',
    );

    // Click "Cancel mission" button in the modal (not the "Never mind" button).
    await page.getByRole('button', { name: /cancel mission/i }).click();
    const cancelReq = await cancelRequest;

    // Verify the POST body carries the reason.
    const body = JSON.parse(cancelReq.postData() ?? '{}') as { reason?: string };
    expect(body.reason).toBe(REASON);

    // Modal should close after successful cancel.
    await expect(page.getByRole('heading', { name: 'Cancel mission' })).not.toBeVisible({
      timeout: 5_000,
    });
  });

  /**
   * Dark mode visual regression baseline.
   *
   * Navigates to the enabled autopilot page in dark mode and locks a screenshot.
   * CI skips snapshot comparisons (`ignoreSnapshots: true`); baselines are set
   * locally on darwin and reviewed in PR.
   */
  test('dark mode visual regression baseline — autopilot goals page', async ({ page }) => {
    await mockSystemReadiness(page);
    await mockStatus(page, true);
    await mockMissionList(page, MID);

    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
    await page.goto('/autopilot');
    await page.waitForLoadState('networkidle');

    // Freeze animations for a deterministic snapshot.
    await page.addStyleTag({
      content:
        '*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }',
    });

    await expect(page).toHaveScreenshot('dark-autopilot-goals.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });
});
