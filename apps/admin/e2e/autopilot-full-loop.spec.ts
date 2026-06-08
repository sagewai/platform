import { test, expect, type Page } from '@playwright/test';
import type { MissionRunEvent } from '@/utils/types';

/**
 * Plan H Task 12 — deterministic full-loop e2e.
 *
 * Exercises the complete user journey:
 *   goal entry → submit → approve → open mission → run → live trace → output
 *
 * All six backend endpoints are mocked at the Playwright route layer.
 * No real LLM, no MissionDriver execution required.
 *
 * Note on mission-output visibility: `MissionDetailView` renders
 * `<AutopilotMissionOutput>` only when `mission.status === 'completed'` AND
 * `traceOutput != null`.  The page refetches the mission once via
 * `handleRunStarted`; that single refetch must return `completed` (plus a
 * non-null trace output) for the output panel to appear.  The
 * `running → completed` transition driven by SSE is already covered in
 * `autopilot-run-button.spec.ts` and `autopilot-live-trace.spec.ts`.
 * Here we exercise the full user journey end-to-end: the second detail fetch
 * returns `completed` immediately so the output panel can be asserted.
 */

// ── SSE helpers ────────────────────────────────────────────────────────────

function sseBody(events: MissionRunEvent[]): string {
  return events
    .map((e) => `event: ${e.kind}\ndata: ${JSON.stringify(e)}\n\n`)
    .join('');
}

function makeEvent(
  overrides: Partial<MissionRunEvent> & Pick<MissionRunEvent, 'event_id' | 'kind'>,
  missionId = 'mid-12',
): MissionRunEvent {
  return {
    ts: new Date().toISOString(),
    mission_id: missionId,
    node_id: 'node-a',
    ...overrides,
  };
}

// ── Mock helpers ───────────────────────────────────────────────────────────

const MID = 'mid-12';

function buildMissionDetail(status: string) {
  return {
    id: MID,
    mission_id: MID,
    status,
    goal_text: 'Summarise this URL: https://example.com',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-full-loop',
    description: 'Fetch the page and return a one-paragraph summary.',
    agent_graph_json: {
      nodes: [
        { id: 'node-a', role: 'fetcher', kind: 'llm', tools: ['http_fetch'], prompt_ref: null },
      ],
      edges: [],
    },
    tools_required: [{ name: 'http_fetch', description: 'Fetch a URL' }],
    providers_required: [{ name: 'primary', tier: 'medium' }],
    slots: { url: 'https://example.com' },
    success_criteria: [],
    training_data_hooks: [],
    estimated_cost: { currency: 'USD', amount: 0.02 },
  };
}

const CANONICAL_TRACE_EVENTS: MissionRunEvent[] = [
  makeEvent({ event_id: 'e-1', kind: 'mission.started', node_id: 'node-a' }),
  makeEvent({ event_id: 'e-2', kind: 'agent.started', node_id: 'node-a' }),
  makeEvent({
    event_id: 'e-3',
    kind: 'agent.tool_call',
    node_id: 'node-a',
    tool: 'http_fetch',
  }),
  makeEvent({
    event_id: 'e-4',
    kind: 'agent.tool_result',
    node_id: 'node-a',
    tool: 'http_fetch',
    output_preview: '<html>Example Domain</html>',
  }),
  makeEvent({
    event_id: 'e-5',
    kind: 'agent.llm_call',
    node_id: 'node-a',
    model: 'claude-haiku-4-5',
    cost_usd: 0.005,
    input_tokens: 200,
    output_tokens: 80,
  }),
  makeEvent({ event_id: 'e-6', kind: 'agent.finished', node_id: 'node-a' }),
  makeEvent({
    event_id: 'e-7',
    kind: 'mission.finished',
    node_id: 'node-a',
    status: 'completed',
    total_cost_usd: 0.005,
  }),
];

const TRACE_OUTPUT = {
  summary: 'Example Domain is a reserved domain used for illustrative purposes in documentation.',
};

async function mockAllEndpoints(page: Page) {
  // 1. Autopilot status — enabled so goal input is shown.
  await page.route('**/api/v1/autopilot/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        enabled: true,
        tier: 'free',
        quota_used: 5,
        quota_limit: 100,
        install_id: 'inst-test',
      }),
    }),
  );

  // 2. POST /api/v1/autopilot/goal → auto_routed result.
  await page.route('**/api/v1/autopilot/goal', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        routing_result: 'auto_routed',
        mission_id: MID,
        blueprint: {
          id: 'bp-full-loop',
          title: 'URL Summariser',
          category: 'content',
          mode: 'batch',
          slots: [{ key: 'url', value: 'https://example.com' }],
          estimated_cost: '~$0.02',
        },
        candidates: [],
        message: null,
      }),
    });
  });

  // 3. POST /api/v1/autopilot/approve → created mission.
  await page.route('**/api/v1/autopilot/approve', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'pending',
        mission_id: MID,
      }),
    });
  });

  // 4. GET /api/v1/autopilot/missions — list used by fetchRecentMissions.
  await page.route(/\/api\/v1\/autopilot\/missions(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback();
    }
    // Don't intercept sub-paths (detail, trace, run, events, etc.)
    const url = route.request().url();
    if (/\/missions\/[^/?]+(\/|$)/.test(url)) return route.fallback();

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        missions: [
          {
            id: MID,
            mission_id: MID,
            blueprint_title: 'URL Summariser',
            blueprint_category: 'content',
            status: 'pending',
            mode: 'batch',
            project_id: null,
            started_at: null,
            finished_at: null,
            steps: [],
          },
        ],
        total: 1,
        count: 1,
      }),
    });
  });

  // 5. GET /api/v1/autopilot/missions/mid-12 — stateful: pending until the run
  //    is POSTed, completed afterwards. Keyed on whether /run was called rather
  //    than on a fetch counter: React Strict Mode double-invokes the detail
  //    effect on mount in dev, so a "first call pending, rest completed" counter
  //    would hand the kept (2nd) fetch a completed mission and hide the Run
  //    button. handleRunStarted's post-run refetch then returns completed.
  let runStarted = false;
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${MID}$`),
    (route) => {
      const status = runStarted ? 'completed' : 'pending';
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildMissionDetail(status)),
      });
    },
  );

  // 6. GET /api/v1/autopilot/missions/mid-12/explain — needed for directions panel.
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${MID}/explain$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          markdown: [
            '## What this will do',
            'Fetch the URL and return a summary.',
            '## Resources allocated',
            '- http_fetch',
            '## How to run',
            'Click **Run mission**.',
            '## How to debug',
            'View the live trace.',
          ].join('\n\n'),
          sections: {
            what_it_does: 'Fetch the URL and return a summary.',
            resources: '- http_fetch',
            how_to_run: 'Click Run mission.',
            how_to_debug: 'View trace.',
          },
        }),
      }),
  );

  // 7. POST /api/v1/autopilot/missions/mid-12/run → 202.
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${MID}/run$`),
    (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      runStarted = true;
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: 'run_x',
          started_at: new Date().toISOString(),
        }),
      });
    },
  );

  // 8. GET /api/v1/autopilot/missions/mid-12/trace — returns completed trace
  //    with all events and output.  The live trace component initializes from
  //    these (initialStatus='completed', initialEvents=[...]) so the agent-row
  //    renders without needing to open SSE.
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${MID}/trace$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mission_id: MID,
          run_id: 'run_x',
          status: 'completed',
          started_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
          last_event_at: new Date().toISOString(),
          total_cost_usd: 0.005,
          step_count: CANONICAL_TRACE_EVENTS.length,
          events: CANONICAL_TRACE_EVENTS,
          output: TRACE_OUTPUT,
          error: null,
        }),
      }),
  );

  // 9. GET /api/v1/autopilot/missions/mid-12/events — SSE stream.
  //    Since the trace initialises with completed status, the component won't
  //    open SSE.  This mock is kept as a safety net in case the SSE is opened
  //    (e.g. on a race condition) and to satisfy any route assertions.
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${MID}/events$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
        body: sseBody(CANONICAL_TRACE_EVENTS),
      }),
  );
}

// ── Full-loop test ─────────────────────────────────────────────────────────

test('goal → approve → run → live trace → output (deterministic mock)', async ({ page }) => {
  await mockAllEndpoints(page);

  // ── Step 1: Visit /autopilot ─────────────────────────────────────────────
  await page.goto('/autopilot');

  // Autopilot is enabled; goal input must be visible.
  const goalInput = page.locator('input[placeholder*="goal" i], input[placeholder*="Describe" i]').first();
  await expect(goalInput).toBeVisible({ timeout: 10_000 });

  // ── Step 2: Submit a goal ────────────────────────────────────────────────
  await goalInput.fill('Summarise this URL: https://example.com');
  await page.getByRole('button', { name: /submit/i }).click();

  // ── Step 3: Approve the routing result ───────────────────────────────────
  // AutopilotPlanPreview renders the blueprint title and "Approve & Schedule" button.
  await expect(page.getByText('URL Summariser').first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole('button', { name: /approve/i }).click();

  // After approval handleApproved() clears the form and fetchRecentMissions() fires.
  // The recent missions list should now show the new mission row.
  await expect(page.getByTestId(`mission-row-${MID}`)).toBeVisible({ timeout: 10_000 });

  // ── Step 4: Navigate to the mission detail ────────────────────────────────
  await page.getByTestId(`mission-row-${MID}`).click();
  await expect(page).toHaveURL(new RegExp(`/autopilot/missions/${MID}$`), { timeout: 10_000 });

  // ── Step 5: Directions panel + enabled Run button ────────────────────────
  const runBtn = page.getByTestId('run-mission-button');
  await expect(runBtn).toBeVisible({ timeout: 10_000 });
  await expect(runBtn).toBeEnabled({ timeout: 5_000 });

  // ── Step 6: Click Run ────────────────────────────────────────────────────
  await runBtn.click();

  // handleRunStarted refetches detail (returns completed) and trace (returns
  // completed + events + output).

  // ── Step 7: Live trace appears ───────────────────────────────────────────
  await expect(page.getByTestId('mission-live-trace')).toBeVisible({ timeout: 10_000 });

  // ── Step 8: Status badge shows completed ─────────────────────────────────
  // The live trace initialises with status='completed' from the trace response.
  await expect(page.getByTestId('status-badge')).toContainText(/completed/i, {
    timeout: 10_000,
  });

  // ── Step 9: At least one agent row is visible ────────────────────────────
  // The trace initialises from CANONICAL_TRACE_EVENTS; node-a has agent.started
  // and other events, so an agent-row is rendered immediately.
  await expect(page.getByTestId('agent-row').first()).toBeVisible({ timeout: 10_000 });

  // ── Step 10: Cost ticker shows accumulated cost ──────────────────────────
  // The trace events include agent.llm_call with cost_usd=0.005.
  await expect(page.getByTestId('cost-ticker')).toContainText('0.005', {
    timeout: 5_000,
  });

  // ── Step 11: Final output panel is visible ───────────────────────────────
  // mission.status='completed' + traceOutput != null → AutopilotMissionOutput renders.
  await expect(page.getByTestId('mission-output')).toBeVisible({ timeout: 10_000 });
});
