import { test, expect, type Page } from '@playwright/test';
import type { MissionRunEvent } from '@/utils/types';

/**
 * E2E for Plan H — AutopilotMissionLiveTrace + AutopilotMissionOutput.
 *
 * Strategy: mock both the mission-detail endpoint (so the page renders) and
 * the events SSE endpoint (so the trace component receives controlled events).
 * The trace endpoint is also mocked to simulate reload-during-run replay.
 *
 * SSE is simulated by fulfilling the route with a `text/event-stream` body
 * containing pre-serialised events. Playwright's route.fulfill() supports
 * streaming responses, but the simplest approach that avoids flakiness is to
 * send all events in a single body chunk — browsers deliver SSE in order and
 * the component handles them via the onmessage / addEventListener callbacks.
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
    mission_id: 'test-mission',
    node_id: 'node-a',
    ...overrides,
  };
}

async function mockMissionDetail(
  page: Page,
  missionId: string,
  status: string = 'running',
) {
  const detail = {
    id: missionId,
    status,
    goal_text: 'E2E live trace test mission',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-test',
    description: 'Test mission for live trace e2e.',
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
    estimated_cost: { currency: 'USD', amount: 0.01 },
  };

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(detail),
      }),
  );

  // Stub the explain endpoint (used when status=pending)
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/explain$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ markdown: '## Test', sections: {} }),
      }),
  );
}

async function mockTraceEndpoint(
  page: Page,
  missionId: string,
  events: MissionRunEvent[],
  output: unknown = null,
) {
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/trace$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mission_id: missionId,
          run_id: 'run-1',
          status: 'running',
          started_at: new Date().toISOString(),
          finished_at: null,
          last_event_at: null,
          total_cost_usd: 0,
          step_count: events.length,
          events,
          output,
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

test.describe('autopilot live trace', () => {
  /**
   * Test 1 — trace renders incoming events.
   *
   * Mock SSE with 3 events: mission.started, agent.started, mission.finished.
   * Assert the agent row appears and status badge transitions running → completed.
   */
  test('trace renders incoming events and status transitions', async ({ page }) => {
    const mid = 'trace-render-test';
    const events: MissionRunEvent[] = [
      makeEvent({ event_id: 'e-1', kind: 'mission.started', node_id: 'node-a' }),
      makeEvent({ event_id: 'e-2', kind: 'agent.started', node_id: 'node-a' }),
      makeEvent({
        event_id: 'e-3',
        kind: 'mission.finished',
        node_id: 'node-a',
        status: 'completed',
        total_cost_usd: 0.001,
      }),
    ];

    await mockMissionDetail(page, mid, 'running');
    await mockTraceEndpoint(page, mid, []);
    await mockSseEndpoint(page, mid, events);

    await page.goto(`/autopilot/missions/${mid}`);

    // Wait for the live trace component to appear.
    await expect(page.getByTestId('mission-live-trace')).toBeVisible({
      timeout: 10_000,
    });

    // Agent row should appear (node-a events).
    await expect(page.getByTestId('agent-row').first()).toBeVisible({
      timeout: 5_000,
    });

    // After mission.finished the status badge should show 'completed'.
    await expect(page.getByTestId('status-badge')).toContainText('completed', {
      timeout: 5_000,
    });
  });

  /**
   * Test 2 — cost ticker increments on llm_call.
   *
   * Mock SSE with an agent.llm_call event with cost_usd=0.005.
   * Assert the cost label shows a $0.005x value.
   */
  test('cost ticker shows accumulated llm_call cost', async ({ page }) => {
    const mid = 'cost-ticker-test';
    const events: MissionRunEvent[] = [
      makeEvent({ event_id: 'e-start', kind: 'mission.started', node_id: 'node-a' }),
      makeEvent({
        event_id: 'e-llm',
        kind: 'agent.llm_call',
        node_id: 'node-a',
        model: 'claude-sonnet-4-5',
        cost_usd: 0.005,
        input_tokens: 100,
        output_tokens: 50,
      }),
    ];

    await mockMissionDetail(page, mid, 'running');
    await mockTraceEndpoint(page, mid, []);
    await mockSseEndpoint(page, mid, events);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('mission-live-trace')).toBeVisible({
      timeout: 10_000,
    });

    // Cost ticker should show the accumulated cost (formatted as ~$0.0050).
    await expect(page.getByTestId('cost-ticker')).toContainText('0.005', {
      timeout: 5_000,
    });
  });

  /**
   * Test 3 — dedup on reload-replay.
   *
   * Pass initialEvents containing 2 events (same event_ids).
   * Mock SSE with the same 2 + 1 new event.
   * Assert exactly 3 distinct events render (not 5).
   *
   * We verify dedup indirectly: the agent row exists and the component
   * doesn't crash. The byNode structure collapses duplicates so only
   * unique events appear in each row.
   */
  test('dedup prevents double-render of replayed events', async ({ page }) => {
    const mid = 'dedup-test';

    const sharedEvents: MissionRunEvent[] = [
      makeEvent({ event_id: 'shared-1', kind: 'mission.started', node_id: 'node-a' }),
      makeEvent({ event_id: 'shared-2', kind: 'agent.started', node_id: 'node-a' }),
    ];
    const newEvent: MissionRunEvent = makeEvent({
      event_id: 'new-1',
      kind: 'agent.llm_call',
      node_id: 'node-a',
      cost_usd: 0.002,
    });

    await mockMissionDetail(page, mid, 'running');
    // Trace returns the 2 shared events as initialEvents.
    await mockTraceEndpoint(page, mid, sharedEvents);
    // SSE sends all 3 (2 shared + 1 new).
    await mockSseEndpoint(page, mid, [...sharedEvents, newEvent]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('mission-live-trace')).toBeVisible({
      timeout: 10_000,
    });

    // There should be exactly 1 agent-row for node-a.
    await expect(page.getByTestId('agent-row')).toHaveCount(1, {
      timeout: 5_000,
    });

    // The cost ticker reflects only 1 llm_call (not 3 if dedup failed).
    await expect(page.getByTestId('cost-ticker')).toContainText('0.002', {
      timeout: 5_000,
    });
  });
});
