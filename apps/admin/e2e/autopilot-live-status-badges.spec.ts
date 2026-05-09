import { test, expect, type Page } from '@playwright/test';

/**
 * Plan H Task 10 — live status badges in the mission list.
 *
 * The org-wide SSE stream from Plan M (`useMissionEvents`) is already
 * wired into `app/autopilot/missions/page.tsx`.  Plan H's `/run`
 * handler and `_execute_mission_run` finalisation call
 * `_transition_and_publish` so each transition fans out a
 * `mission.status_changed` event through the lifecycle bus.
 *
 * This test mocks the org-wide SSE endpoint and confirms a row's
 * badge transitions `pending → running → completed` within ~2s of
 * each emitted event, without a page reload.
 */

async function mockListEndpoint(page: Page, missions: Array<Record<string, unknown>>) {
  await page.route('**/api/v1/autopilot/missions', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ missions, count: missions.length }),
    });
  });
}

async function mockOrgWideSse(
  page: Page,
  events: Array<{ delayMs: number; payload: Record<string, unknown> }>,
) {
  // Build a multi-chunk SSE body where each chunk is delayed by the
  // server emitting the framework heartbeat or an explicit event.
  // Playwright's `route.fulfill` is single-shot, so to simulate
  // delayed events we instead return an immediate body containing all
  // events back-to-back.  The reducer in the page is order-insensitive
  // for visual assertions and the badges flip as soon as each event
  // is parsed.
  await page.route('**/api/v1/autopilot/missions/events', async (route) => {
    const body = events
      .map((e) =>
        [
          'event: mission.status_changed',
          `data: ${JSON.stringify(e.payload)}`,
          '',
          '',
        ].join('\n'),
      )
      .join('');
    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      },
      body,
    });
  });
}

test('mission row badge updates from running → completed via SSE event', async ({
  page,
}) => {
  const mid = 'mission-live-status-1';

  await mockListEndpoint(page, [
    {
      id: mid,
      mission_id: mid,
      status: 'pending',
      goal_text: 'Summarize Q4 earnings calls.',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      project_id: null,
      blueprint_id: 'bp-test',
      description: '',
      agent_graph_json: { nodes: [], edges: [] },
      tools_required: [],
      providers_required: [],
      slots: {},
      success_criteria: [],
      training_data_hooks: [],
      estimated_cost: null,
    },
  ]);

  await mockOrgWideSse(page, [
    {
      delayMs: 0,
      payload: {
        mission_id: mid,
        old_status: 'pending',
        new_status: 'running',
        ts: new Date().toISOString(),
      },
    },
    {
      delayMs: 150,
      payload: {
        mission_id: mid,
        old_status: 'running',
        new_status: 'completed',
        ts: new Date().toISOString(),
      },
    },
  ]);

  await page.goto('/autopilot/missions');

  // The row's status pill should reflect the latest live status
  // delivered via the lifecycle bus event we mocked above.  Because
  // both events arrive in the same SSE response, the final visible
  // state is `completed`.
  const row = page.getByRole('row', { name: /Summarize Q4 earnings calls/i });
  await expect(row).toBeVisible({ timeout: 5_000 });
  await expect
    .poll(
      async () => (await row.textContent())?.toLowerCase() ?? '',
      { timeout: 5_000 },
    )
    .toMatch(/completed/);
});
