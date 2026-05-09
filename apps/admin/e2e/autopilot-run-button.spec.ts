import { test, expect, type Page } from '@playwright/test';
import type { MissionRunEvent } from '@/utils/types';

/**
 * E2E for Plan H Task 9 — Run button on the autopilot mission detail page.
 *
 * Strategy: mock all backend endpoints with `page.route` so tests are
 * independent of the hosted blueprint service. The run endpoint is mocked
 * to return a run_id, and the second detail fetch (refetch after run-started)
 * returns the mission with status=running to exercise the directions→live-trace
 * view swap.
 */

// ── Helpers ────────────────────────────────────────────────────────────────

function sseBody(events: MissionRunEvent[]): string {
  return events
    .map((e) => `event: ${e.kind}\ndata: ${JSON.stringify(e)}\n\n`)
    .join('');
}

function makeEvent(
  overrides: Partial<MissionRunEvent> & Pick<MissionRunEvent, 'event_id' | 'kind'>,
): MissionRunEvent {
  return {
    ts: new Date().toISOString(),
    mission_id: 'run-btn-test',
    node_id: 'node-a',
    ...overrides,
  };
}

function buildDetail(missionId: string, status: string) {
  return {
    id: missionId,
    mission_id: missionId,
    status,
    goal_text: 'Run button test mission',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-run-test',
    description: 'Test mission for run button e2e.',
    agent_graph_json: {
      nodes: [
        { id: 'node-a', role: 'executor', kind: 'llm', tools: [], prompt_ref: null },
      ],
      edges: [],
    },
    tools_required: [],
    providers_required: [],
    slots: {},
    success_criteria: [],
    training_data_hooks: [],
    estimated_cost: { currency: 'USD', amount: 0.05 },
  };
}

/**
 * Mock the mission detail endpoint with a stateful counter so the first call
 * returns `initialStatus` and subsequent calls return `runningStatus`.
 */
async function mockMissionDetailSequence(
  page: Page,
  missionId: string,
  initialStatus: string,
  runningStatus: string,
) {
  let callCount = 0;
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}$`),
    (route) => {
      callCount += 1;
      const status = callCount === 1 ? initialStatus : runningStatus;
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildDetail(missionId, status)),
      });
    },
  );
}

async function mockMissionDetailFixed(
  page: Page,
  missionId: string,
  status: string,
) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(buildDetail(missionId, status)),
      }),
  );
}

async function mockExplainEndpoint(page: Page, missionId: string) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/explain$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          markdown: '## What this will do\n\nRun the mission.',
          sections: {},
        }),
      }),
  );
}

async function mockRunEndpoint(
  page: Page,
  missionId: string,
  responseStatus: number = 200,
) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/run$`),
    (route) => {
      if (responseStatus !== 200) {
        return route.fulfill({
          status: responseStatus,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Internal server error' }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: `run_${missionId}`,
          started_at: new Date().toISOString(),
        }),
      });
    },
  );
}

async function mockTraceEndpoint(
  page: Page,
  missionId: string,
  events: MissionRunEvent[] = [],
) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/trace$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mission_id: missionId,
          run_id: `run_${missionId}`,
          status: 'running',
          started_at: new Date().toISOString(),
          finished_at: null,
          last_event_at: null,
          total_cost_usd: 0,
          step_count: events.length,
          events,
          output: null,
          error: null,
        }),
      }),
  );
}

async function mockSseEndpoint(
  page: Page,
  missionId: string,
  events: MissionRunEvent[],
) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/events$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: {
          'Cache-Control': 'no-cache',
          'X-Accel-Buffering': 'no',
        },
        body: sseBody(events),
      }),
  );
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('autopilot run button', () => {
  /**
   * Test 1 — clicking Run posts to /run then swaps directions → live trace.
   *
   * Flow:
   *   1. First detail fetch → status=pending → directions panel shown.
   *   2. Click "Run mission" → POST /run returns {run_id, started_at}.
   *   3. onRunStarted fires → second detail fetch → status=running.
   *   4. Trace fetch → returns empty events.
   *   5. SSE → delivers mission.started event.
   *   6. Live trace component becomes visible; status badge shows "running".
   */
  test('Run button posts then swaps to live trace', async ({ page }) => {
    const mid = 'run-btn-swap';

    // First call returns pending; subsequent calls return running.
    await mockMissionDetailSequence(page, mid, 'pending', 'running');
    await mockExplainEndpoint(page, mid);
    await mockRunEndpoint(page, mid, 200);
    await mockTraceEndpoint(page, mid, []);

    const sseEvents: MissionRunEvent[] = [
      makeEvent({ event_id: 'e-1', kind: 'mission.started', node_id: 'node-a', mission_id: mid }),
    ];
    await mockSseEndpoint(page, mid, sseEvents);

    await page.goto(`/autopilot/missions/${mid}`);

    // Should be in directions view — Run button enabled.
    const runBtn = page.getByTestId('run-mission-button');
    await expect(runBtn).toBeVisible({ timeout: 10_000 });
    await expect(runBtn).toBeEnabled();

    await runBtn.click();

    // After refetch with running status, live trace should appear.
    await expect(page.getByTestId('mission-live-trace')).toBeVisible({
      timeout: 10_000,
    });

    // Status badge should show running.
    await expect(page.getByTestId('status-badge')).toContainText('running', {
      timeout: 5_000,
    });
  });

  /**
   * Test 2 — Run button is hidden for terminal missions.
   *
   * For a completed mission the Run button should not be in the DOM at all.
   */
  test('Run button hides for terminal mission', async ({ page }) => {
    const mid = 'run-btn-terminal';

    await mockMissionDetailFixed(page, mid, 'completed');
    // Explain not needed for completed missions (directions panel hidden).
    // Trace endpoint needed since status is terminal.
    await mockTraceEndpoint(page, mid, []);
    await mockSseEndpoint(page, mid, []);

    await page.goto(`/autopilot/missions/${mid}`);

    // Wait for header to render.
    await expect(page.getByTestId('mission-header')).toBeVisible({
      timeout: 10_000,
    });

    // Run button should not be present in the DOM for terminal missions.
    await expect(page.getByTestId('run-mission-button')).toHaveCount(0);
    await expect(page.getByTestId('run-mission-button-running')).toHaveCount(0);
  });

  /**
   * Test 3 — Run button surfaces error inline on /run 500 failure.
   *
   * Click the Run button when the backend returns 500.
   * The button should re-enable and an inline error should appear under
   * data-testid="run-error".
   */
  test('Run button surfaces error inline on failure', async ({ page }) => {
    const mid = 'run-btn-error';

    await mockMissionDetailFixed(page, mid, 'pending');
    await mockExplainEndpoint(page, mid);
    await mockRunEndpoint(page, mid, 500);

    await page.goto(`/autopilot/missions/${mid}`);

    const runBtn = page.getByTestId('run-mission-button');
    await expect(runBtn).toBeVisible({ timeout: 10_000 });
    await expect(runBtn).toBeEnabled();

    await runBtn.click();

    // Error container should appear.
    await expect(page.getByTestId('run-error')).toBeVisible({ timeout: 5_000 });

    // Button should be re-enabled after failure.
    await expect(runBtn).toBeEnabled({ timeout: 5_000 });
  });
});
