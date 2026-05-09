import { test, expect, type Page } from '@playwright/test';

/**
 * E2E smoke tests for Plan J — AutopilotSandboxPanel.
 *
 * Strategy: mock mission-detail and sandbox-allocation endpoints so
 * Playwright drives the panel without a running backend.
 */

// ── Helpers ────────────────────────────────────────────────────────────────

async function mockMissionDetail(page: Page, missionId: string) {
  const detail = {
    id: missionId,
    status: 'pending',
    goal_text: 'Sandbox tier e2e test',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-sandbox-e2e',
    description: 'Sandbox e2e mission.',
    agent_graph_json: {
      nodes: [
        { id: 'step-read', role: 'reader', kind: 'llm', tools: ['read_file'], prompt_ref: null },
        { id: 'step-search', role: 'researcher', kind: 'llm', tools: ['web_search'], prompt_ref: null },
        { id: 'step-exec', role: 'executor', kind: 'llm', tools: ['shell_exec'], prompt_ref: null },
      ],
      edges: [],
    },
    tools_required: ['read_file', 'web_search', 'shell_exec'],
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

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/explain$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ markdown: '## Plan', sections: {} }),
      }),
  );

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/trace$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mission_id: missionId,
          run_id: null,
          status: 'pending',
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

async function mockSandboxAllocation(
  page: Page,
  missionId: string,
  steps: Array<{ step_id: string; role: string; tools: string[]; tier: string; overridden?: boolean }>,
) {
  const allocation = steps.map((s) => ({
    step_id: s.step_id,
    role: s.role,
    tools: s.tools,
    tier: s.tier,
    base_tier: s.tier,
    overridden: s.overridden ?? false,
  }));

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/sandbox-allocation$`),
    (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(allocation) }),
  );
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('autopilot sandbox panel', () => {
  test('panel renders tier badges for all steps', async ({ page }) => {
    const mid = 'sandbox-e2e-basic';

    await mockMissionDetail(page, mid);
    await mockSandboxAllocation(page, mid, [
      { step_id: 'step-read', role: 'reader', tools: ['read_file'], tier: 'TRUSTED' },
      { step_id: 'step-search', role: 'researcher', tools: ['web_search'], tier: 'SANDBOXED' },
      { step_id: 'step-exec', role: 'executor', tools: ['shell_exec'], tier: 'UNTRUSTED' },
    ]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('sandbox-panel')).toBeVisible({ timeout: 10_000 });

    const badges = page.getByTestId('tier-badge');
    await expect(badges).toHaveCount(3, { timeout: 5_000 });

    await expect(page.getByTestId('sandbox-panel')).toContainText('TRUSTED', { timeout: 5_000 });
    await expect(page.getByTestId('sandbox-panel')).toContainText('SANDBOXED', { timeout: 5_000 });
    await expect(page.getByTestId('sandbox-panel')).toContainText('UNTRUSTED', { timeout: 5_000 });
  });

  test('panel shows empty state when no steps', async ({ page }) => {
    const mid = 'sandbox-e2e-empty';

    await mockMissionDetail(page, mid);
    await mockSandboxAllocation(page, mid, []);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('sandbox-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('sandbox-panel')).toContainText('No agent steps', {
      timeout: 5_000,
    });
  });

  test('overridden step shows pencil indicator', async ({ page }) => {
    const mid = 'sandbox-e2e-overridden';

    await mockMissionDetail(page, mid);
    await mockSandboxAllocation(page, mid, [
      { step_id: 'step-read', role: 'reader', tools: ['read_file'], tier: 'SANDBOXED', overridden: true },
    ]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('sandbox-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('tier-badge')).toContainText('SANDBOXED', { timeout: 5_000 });
    await expect(page.getByTestId('tier-badge')).toContainText('✎', { timeout: 5_000 });
  });
});
