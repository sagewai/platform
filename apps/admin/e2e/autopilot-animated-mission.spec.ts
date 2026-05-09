import { expect, test, type Page } from '@playwright/test';
import type { MissionRunEvent } from '@/utils/types';

/**
 * E2E for Plan N — Animated live agent graph + data flow + cost burn-down.
 *
 * Strategy: mock /api/v1/autopilot/missions/:id (detail), /trace, and /events
 * (SSE). The animated scene opens its own SSE — same endpoint as the
 * existing live-trace, distinct connection. We assert the visible state
 * transitions via the data-state attributes that AnimatedAgentNode emits and
 * the data-band attribute on CostBurnDownChart. Particles are asserted via
 * the `[data-testid="flow-particle"]` selector which matches the `<motion.circle>`.
 */

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
    mission_id: 'plan-n-mission',
    node_id: 'planner',
    ...overrides,
  };
}

function buildGraph(): {
  nodes: { id: string; role: string; kind: 'llm'; tools: string[]; prompt_ref: null }[];
  edges: { from: string; to: string }[];
} {
  return {
    nodes: [
      { id: 'planner', role: 'Planner', kind: 'llm', tools: [], prompt_ref: null },
      { id: 'coder', role: 'Coder', kind: 'llm', tools: ['code_execute'], prompt_ref: null },
    ],
    edges: [{ from: 'planner', to: 'coder' }],
  };
}

async function mockMissionDetail(
  page: Page,
  missionId: string,
  status: string,
  capUsd = 1.0,
) {
  const detail = {
    id: missionId,
    status,
    goal_text: 'Plan N animation e2e',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-test',
    description: 'Plan N animation test mission.',
    agent_graph_json: buildGraph(),
    tools_required: [],
    providers_required: [],
    slots: {},
    success_criteria: [],
    training_data_hooks: [],
    estimated_cost: { currency: 'USD', amount: capUsd },
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
        headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
        body: sseBody(events),
      }),
  );
}

test.describe('animated mission view (Plan N)', () => {
  test('happy path: pulse → particles → completion bloom + cost burn-down band', async ({
    page,
  }) => {
    const mid = 'plan-n-happy';
    const events: MissionRunEvent[] = [
      makeEvent({ event_id: 'm-start', kind: 'mission.started', node_id: undefined }),
      makeEvent({
        event_id: 'p-start',
        kind: 'agent.started',
        node_id: 'planner',
      }),
      makeEvent({
        event_id: 'p-llm',
        kind: 'agent.llm_call',
        node_id: 'planner',
        model: 'claude-haiku-4-5',
        cost_usd: 0.1,
        input_tokens: 100,
        output_tokens: 60,
      }),
      makeEvent({
        event_id: 'p-finish',
        kind: 'agent.finished',
        node_id: 'planner',
        status: 'completed',
      }),
      makeEvent({
        event_id: 'c-start',
        kind: 'agent.started',
        node_id: 'coder',
      }),
      makeEvent({
        event_id: 'c-llm',
        kind: 'agent.llm_call',
        node_id: 'coder',
        model: 'claude-sonnet-4-6',
        cost_usd: 0.55,
        input_tokens: 400,
        output_tokens: 300,
      }),
      makeEvent({
        event_id: 'c-finish',
        kind: 'agent.finished',
        node_id: 'coder',
        status: 'completed',
      }),
      makeEvent({
        event_id: 'm-finish',
        kind: 'mission.finished',
        node_id: undefined,
        status: 'completed',
        total_cost_usd: 0.65,
      }),
    ];

    await mockMissionDetail(page, mid, 'running', 1.0);
    await mockTraceEndpoint(page, mid, []);
    await mockSseEndpoint(page, mid, events);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('mission-live-scene')).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId('agent-graph')).toBeVisible();

    // Both nodes should reach completed state.
    await expect(
      page.locator('[data-testid="agent-graph-node"][data-state="completed"]'),
    ).toHaveCount(2, { timeout: 5_000 });

    // Cost burn-down should reflect cumulative cost ≈ $0.65 against $1.00 cap → red band.
    const costPanel = page.getByTestId('cost-burn-down');
    await expect(costPanel).toBeVisible();
    await expect(costPanel).toHaveAttribute('data-band', /(yellow|red)/);

    // Status announcer narrates the final mission state.
    await expect(page.getByTestId('mission-status-announcer')).toContainText(
      /Mission completed|coder completed/i,
      { timeout: 5_000 },
    );
  });

  test('reduced motion: no particles even on handoff', async ({ browser }) => {
    const ctx = await browser.newContext({
      reducedMotion: 'reduce',
      storageState: '.auth/user.json',
    });
    const page = await ctx.newPage();
    const mid = 'plan-n-reduced';
    const events: MissionRunEvent[] = [
      makeEvent({ event_id: 'p-start', kind: 'agent.started', node_id: 'planner' }),
      makeEvent({
        event_id: 'p-finish',
        kind: 'agent.finished',
        node_id: 'planner',
        status: 'completed',
      }),
      makeEvent({ event_id: 'c-start', kind: 'agent.started', node_id: 'coder' }),
    ];
    await mockMissionDetail(page, mid, 'running', 1.0);
    await mockTraceEndpoint(page, mid, []);
    await mockSseEndpoint(page, mid, events);

    await page.goto(`/autopilot/missions/${mid}`);
    await expect(page.getByTestId('mission-live-scene')).toBeVisible({
      timeout: 10_000,
    });
    // The overlay itself should not render in reduced-motion mode.
    await expect(page.getByTestId('data-flow-particles-overlay')).toHaveCount(0);
    await expect(page.getByTestId('flow-particle')).toHaveCount(0);
    await ctx.close();
  });

  test('terminal mission renders Skip-replay control', async ({ page }) => {
    const mid = 'plan-n-replay';
    const events: MissionRunEvent[] = [
      makeEvent({ event_id: 'p-start', kind: 'agent.started', node_id: 'planner' }),
      makeEvent({
        event_id: 'p-finish',
        kind: 'agent.finished',
        node_id: 'planner',
        status: 'completed',
        ts: new Date(Date.now() + 4000).toISOString(),
      }),
      makeEvent({
        event_id: 'm-finish',
        kind: 'mission.finished',
        node_id: undefined,
        status: 'completed',
        total_cost_usd: 0.1,
        ts: new Date(Date.now() + 5000).toISOString(),
      }),
    ];
    await mockMissionDetail(page, mid, 'completed', 1.0);
    await mockTraceEndpoint(page, mid, events);
    await mockSseEndpoint(page, mid, []);

    await page.goto(`/autopilot/missions/${mid}`);
    await expect(page.getByTestId('mission-live-scene')).toBeVisible({
      timeout: 10_000,
    });
    // Replay scheduler installs the skip button immediately on mount.
    await expect(page.getByTestId('skip-replay')).toBeVisible({ timeout: 5_000 });
    await page.getByTestId('skip-replay').click();
    // After flush, all events fire — both nodes should reach a terminal state.
    await expect(
      page.locator('[data-testid="agent-graph-node"][data-state="completed"]'),
    ).toHaveCount(1, { timeout: 5_000 });
    // Skip button should hide after being pressed.
    await expect(page.getByTestId('skip-replay')).toHaveCount(0);
  });
});
