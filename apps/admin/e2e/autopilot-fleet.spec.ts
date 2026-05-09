import { test, expect, type Page } from '@playwright/test';

/**
 * E2E smoke tests for Plan I — AutopilotFleetPanel.
 *
 * Strategy: mock the three fleet API endpoints (mission detail, fleet/workers,
 * fleet-allocation) so Playwright can drive the panel without a running backend.
 */

// ── Helpers ────────────────────────────────────────────────────────────────

async function mockMissionDetail(page: Page, missionId: string, status = 'pending') {
  const detail = {
    id: missionId,
    status,
    goal_text: 'Fleet integration e2e test',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-fleet-e2e',
    description: 'Fleet e2e mission.',
    agent_graph_json: {
      nodes: [{ id: 'step-1', role: 'researcher', kind: 'llm', tools: ['web_search'], prompt_ref: null }],
      edges: [],
    },
    tools_required: ['web_search'],
    providers_required: [],
    slots: {},
    success_criteria: [],
    training_data_hooks: [],
    estimated_cost: { currency: 'USD', amount: 0.01 },
  };

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}$`),
    (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail) }),
  );

  // Explain endpoint stub (shown for pending status)
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/explain$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ markdown: '## Plan', sections: {} }),
      }),
  );

  // Trace endpoint stub
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/trace$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mission_id: missionId,
          run_id: null,
          status,
          started_at: null,
          finished_at: null,
          last_event_at: null,
          total_cost_usd: 0,
          step_count: 0,
          events: [],
          output: null,
          error: null,
        }),
      }),
  );
}

async function mockWorkers(page: Page, count: number) {
  const workers = Array.from({ length: count }, (_, i) => ({
    id: `w${i + 1}`,
    name: `worker-${i + 1}`,
    models_canonical: ['claude-sonnet-4-5'],
    pool: 'default',
    probe_status: null,
  }));

  await page.route(
    /\/api\/v1\/autopilot\/fleet\/workers$/,
    (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(workers) }),
  );
}

async function mockFleetAllocation(
  page: Page,
  missionId: string,
  steps: Array<{
    step_id: string;
    matched_workers: number;
    claimed_worker_id?: string;
  }>,
) {
  const allocation = steps.map((s) => ({
    step_id: s.step_id,
    agent_id: s.step_id,
    role: 'researcher',
    tools: ['web_search'],
    matched_workers: Array.from({ length: s.matched_workers }, (_, i) => ({
      worker_id: `w${i + 1}`,
      worker_name: `worker-${i + 1}`,
      probe_status: null,
    })),
    claimed_worker_id: s.claimed_worker_id ?? null,
  }));

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/fleet-allocation$`),
    (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(allocation) }),
  );
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('autopilot fleet panel', () => {
  test('fleet panel renders with worker count and step allocation', async ({ page }) => {
    const mid = 'fleet-e2e-basic';

    await mockMissionDetail(page, mid, 'pending');
    await mockWorkers(page, 3);
    await mockFleetAllocation(page, mid, [{ step_id: 'step-1', matched_workers: 2 }]);

    await page.goto(`/autopilot/missions/${mid}`);

    // Fleet panel should be present
    await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 10_000 });

    // Worker count shows "3 workers"
    await expect(page.getByTestId('fleet-panel')).toContainText('3 workers', { timeout: 5_000 });

    // Idle count: 3 idle (none degraded)
    await expect(page.getByTestId('fleet-panel')).toContainText('3 idle', { timeout: 5_000 });

    // Step row shows 2 compatible workers
    await expect(page.getByTestId('fleet-panel')).toContainText('2 compatible worker', {
      timeout: 5_000,
    });
  });

  test('fleet panel shows no-worker error when pool is empty', async ({ page }) => {
    const mid = 'fleet-e2e-no-worker';

    await mockMissionDetail(page, mid, 'pending');
    await mockWorkers(page, 0);
    await mockFleetAllocation(page, mid, [{ step_id: 'step-1', matched_workers: 0 }]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 10_000 });

    // 0 workers
    await expect(page.getByTestId('fleet-panel')).toContainText('0 workers', { timeout: 5_000 });

    // Warning for no compatible workers
    await expect(page.getByTestId('fleet-panel')).toContainText('No compatible workers in pool', {
      timeout: 5_000,
    });
  });

  test('claimed step shows worker name', async ({ page }) => {
    const mid = 'fleet-e2e-claimed';

    await mockMissionDetail(page, mid, 'running');
    await mockWorkers(page, 1);
    await mockFleetAllocation(page, mid, [
      { step_id: 'step-1', matched_workers: 1, claimed_worker_id: 'w1' },
    ]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('fleet-panel')).toBeVisible({ timeout: 10_000 });

    // Claimed state should show worker name or id
    await expect(page.getByTestId('fleet-panel')).toContainText('Claimed by', { timeout: 5_000 });
    await expect(page.getByTestId('fleet-panel')).toContainText('w1', { timeout: 5_000 });
  });
});
