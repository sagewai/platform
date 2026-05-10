// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import { test, expect, type Page } from '@playwright/test';
import type { MissionRunEvent } from '@/utils/types';

/**
 * Plan O — Mission timeline scrubber + step-snapshot panel.
 *
 * All API calls are mocked (page.route) so the test is hermetic —
 * no real backend needed. The scrubber is only visible on terminal
 * missions (completed | failed | cancelled) with a non-empty trace.
 */

function makeEvent(
  overrides: Partial<MissionRunEvent> & Pick<MissionRunEvent, 'event_id' | 'kind'>,
): MissionRunEvent {
  return {
    ts: new Date().toISOString(),
    mission_id: 'fixture-completed',
    node_id: 'planner',
    ...overrides,
  };
}

const TRACE_EVENTS: MissionRunEvent[] = [
  makeEvent({ event_id: 'e1', kind: 'mission.started', ts: '2026-05-09T10:00:00.000Z' }),
  makeEvent({
    event_id: 'e2',
    kind: 'agent.tool_call',
    ts: '2026-05-09T10:00:01.000Z',
    tool: 'web_search',
    latency_ms: 230,
    cost_usd: 0.001,
    output_preview: 'Found 5 results for "Berlin flights"',
  }),
  makeEvent({
    event_id: 'e3',
    kind: 'agent.finished',
    ts: '2026-05-09T10:00:02.500Z',
    node_id: 'planner',
    status: 'success',
    output_preview: 'Mission complete.',
  }),
];

async function mockMission(page: Page) {
  // NOTE: Playwright matches routes in LIFO order — last registered = highest priority.
  // Register the catch-all FIRST so specific routes (trace, explain) registered after
  // it take precedence.

  const detail = {
    id: 'fixture-completed',
    status: 'completed',
    goal_text: 'Plan O replay e2e',
    created_at: '2026-05-09T10:00:00.000Z',
    updated_at: '2026-05-09T10:00:02.500Z',
    project_id: null,
    blueprint_id: 'bp-test',
    description: 'Fixture mission for Plan O tests.',
    agent_graph_json: {
      nodes: [{ id: 'planner', role: 'Planner', kind: 'llm', tools: [], prompt_ref: null }],
      edges: [],
    },
    slots: {},
    tools_required: [],
    providers_required: [],
    success_criteria: [],
    training_data_hooks: [],
    estimated_cost: { amount: 0.01, currency: 'USD' },
    started_at: '2026-05-09T10:00:00.000Z',
    finished_at: '2026-05-09T10:00:02.500Z',
    total_cost_usd: 0.001,
    step_count: 1,
  };

  const trace = {
    mission_id: 'fixture-completed',
    run_id: 'run-fixture',
    status: 'completed',
    started_at: '2026-05-09T10:00:00.000Z',
    finished_at: '2026-05-09T10:00:02.500Z',
    last_event_at: '2026-05-09T10:00:02.500Z',
    total_cost_usd: 0.001,
    step_count: 1,
    events: TRACE_EVENTS,
    output: 'Mission completed successfully.',
    error: null,
  };

  // 1st registered = lowest LIFO priority: catch-all for fleet/sandbox/sealed sub-routes.
  await page.route('**/api/v1/autopilot/missions/fixture-completed/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
  );
  // 2nd: exact mission detail (overrides catch-all for the root path).
  await page.route('**/api/v1/autopilot/missions/fixture-completed', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail) }),
  );
  // 3rd: trace (higher priority than catch-all).
  await page.route('**/api/v1/autopilot/missions/fixture-completed/trace', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(trace) }),
  );
  // 4th (highest): explain.
  await page.route('**/api/v1/autopilot/missions/fixture-completed/explain', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ brief: '' }) }),
  );
}

test.describe('Mission timeline scrubber', () => {
  test('scrubber is visible for completed mission with trace', async ({ page }) => {
    await mockMission(page);
    await page.goto('/autopilot/missions/fixture-completed');
    await expect(page.getByTestId('mission-timeline-scrubber')).toBeVisible({ timeout: 8000 });
  });

  test('speed preset buttons are present', async ({ page }) => {
    await mockMission(page);
    await page.goto('/autopilot/missions/fixture-completed');
    const scrubber = page.getByTestId('mission-timeline-scrubber');
    await expect(scrubber).toBeVisible({ timeout: 8000 });
    for (const label of ['0.5×', '1×', '2×', '4×']) {
      await expect(scrubber.getByRole('button', { name: label })).toBeVisible();
    }
  });

  test('speed preset toggle changes aria-pressed', async ({ page }) => {
    await mockMission(page);
    await page.goto('/autopilot/missions/fixture-completed');
    await expect(page.getByTestId('mission-timeline-scrubber')).toBeVisible({ timeout: 8000 });
    const btn2x = page.getByRole('button', { name: '2×' });
    await btn2x.click();
    await expect(btn2x).toHaveAttribute('aria-pressed', 'true');
  });

  test('timeline slider is rendered with correct range', async ({ page }) => {
    await mockMission(page);
    await page.goto('/autopilot/missions/fixture-completed');
    const slider = page.getByTestId('timeline-slider');
    await expect(slider).toBeVisible({ timeout: 8000 });
    await expect(slider).toHaveAttribute('min', '0');
    await expect(slider).toHaveAttribute('max', String(TRACE_EVENTS.length - 1));
  });

  test('dragging slider updates step snapshot panel', async ({ page }) => {
    await mockMission(page);
    await page.goto('/autopilot/missions/fixture-completed');
    const slider = page.getByTestId('timeline-slider');
    await expect(slider).toBeVisible({ timeout: 8000 });
    // Seek to the last event (index 2).
    await slider.fill('2');
    // Snapshot panel should surface the event kind.
    await expect(page.getByTestId('step-snapshot-panel')).toBeVisible({ timeout: 4000 });
  });

  test('play button is present and labeled', async ({ page }) => {
    await mockMission(page);
    await page.goto('/autopilot/missions/fixture-completed');
    await expect(page.getByTestId('play-replay')).toBeVisible({ timeout: 8000 });
  });
});

test.describe('Step snapshot panel', () => {
  test('copy JSON button is present', async ({ page }) => {
    await mockMission(page);
    await page.goto('/autopilot/missions/fixture-completed');
    // Drag to index 1 to ensure a specific event is shown.
    const slider = page.getByTestId('timeline-slider');
    await expect(slider).toBeVisible({ timeout: 8000 });
    await slider.fill('1');
    const panel = page.getByTestId('step-snapshot-panel');
    await expect(panel).toBeVisible({ timeout: 4000 });
    await expect(panel.getByRole('button', { name: 'Copy JSON' })).toBeVisible();
  });
});

test.describe('Scrubber hidden for running missions', () => {
  test('timeline scrubber absent when mission is running', async ({ page }) => {
    const detail = {
      id: 'fixture-running',
      status: 'running',
      goal_text: 'Running mission',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      project_id: null,
      blueprint_id: 'bp-test',
      description: '',
      agent_graph_json: {
        nodes: [{ id: 'a', role: 'Agent', kind: 'llm', tools: [], prompt_ref: null }],
        edges: [],
      },
      slots: {},
      tools_required: [],
      providers_required: [],
      success_criteria: [],
      training_data_hooks: [],
      estimated_cost: null,
      started_at: new Date().toISOString(),
      finished_at: null,
      total_cost_usd: 0,
      step_count: 0,
    };
    // Catch-all first (lowest LIFO priority) so the exact route registered after wins.
    await page.route('**/api/v1/autopilot/missions/fixture-running/**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
    );
    await page.route('**/api/v1/autopilot/missions/fixture-running', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail) }),
    );
    await page.goto('/autopilot/missions/fixture-running');
    // Give the page time to settle then assert absent.
    await page.waitForTimeout(1000);
    await expect(page.getByTestId('mission-timeline-scrubber')).not.toBeVisible();
  });
});
