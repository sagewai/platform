import { test, expect, type Page } from '@playwright/test';

/**
 * E2E smoke tests for Plan K — AutopilotSealedPanel.
 *
 * Strategy: mock mission-detail and sealed-allocation endpoints so
 * Playwright drives the panel without a running backend.
 */

// ── Helpers ────────────────────────────────────────────────────────────────

async function mockMissionDetail(page: Page, missionId: string) {
  const detail = {
    id: missionId,
    status: 'pending',
    goal_text: 'Sealed panel e2e test',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-sealed-e2e',
    description: 'Sealed e2e mission.',
    agent_graph_json: {
      nodes: [
        { id: 'step-read', role: 'reader', kind: 'llm', tools: ['read_file'], prompt_ref: null },
        { id: 'step-exec', role: 'executor', kind: 'llm', tools: ['shell_exec'], prompt_ref: null },
      ],
      edges: [],
    },
    tools_required: ['read_file', 'shell_exec'],
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

async function mockSealedAllocation(
  page: Page,
  missionId: string,
  steps: Array<{
    step_id: string;
    role: string;
    tools: string[];
    required_scopes: string[];
    matched_profile_id: string | null;
    overridden?: boolean;
    jit_hitl?: boolean;
  }>,
) {
  const allocation = steps.map((s) => ({
    step_id: s.step_id,
    role: s.role,
    tools: s.tools,
    required_scopes: s.required_scopes,
    matched_profile_id: s.matched_profile_id,
    overridden: s.overridden ?? false,
    jit_hitl: s.jit_hitl ?? false,
  }));

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${missionId}/sealed-allocation$`),
    (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(allocation) }),
  );
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('autopilot sealed panel', () => {
  test('panel renders matched profiles and JIT-HITL pills', async ({ page }) => {
    const mid = 'sealed-e2e-basic';

    await mockMissionDetail(page, mid);
    await mockSealedAllocation(page, mid, [
      {
        step_id: 'step-read',
        role: 'reader',
        tools: ['read_file'],
        required_scopes: ['fs.read'],
        matched_profile_id: 'p-fs',
      },
      {
        step_id: 'step-exec',
        role: 'executor',
        tools: ['shell_exec'],
        required_scopes: ['exec.shell'],
        matched_profile_id: null,
        jit_hitl: true,
      },
    ]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('sealed-panel')).toBeVisible({ timeout: 10_000 });

    // Matched profile shown
    await expect(page.getByTestId('sealed-panel')).toContainText('p-fs', { timeout: 5_000 });

    // JIT-HITL pill shown for unmatched step
    await expect(page.getByTestId('jit-hitl-pill')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId('jit-hitl-pill')).toContainText('JIT-HITL pending');
  });

  test('JIT-HITL count badge shows in panel header', async ({ page }) => {
    const mid = 'sealed-e2e-count';

    await mockMissionDetail(page, mid);
    await mockSealedAllocation(page, mid, [
      {
        step_id: 'step-a',
        role: 'executor',
        tools: ['shell_exec'],
        required_scopes: ['exec.shell'],
        matched_profile_id: null,
        jit_hitl: true,
      },
      {
        step_id: 'step-b',
        role: 'executor',
        tools: ['docker_run'],
        required_scopes: ['exec.docker'],
        matched_profile_id: null,
        jit_hitl: true,
      },
    ]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('sealed-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('jit-hitl-count')).toContainText('2 JIT-HITL', {
      timeout: 5_000,
    });
  });

  test('step with no scopes shows no profile required', async ({ page }) => {
    const mid = 'sealed-e2e-noscope';

    await mockMissionDetail(page, mid);
    await mockSealedAllocation(page, mid, [
      {
        step_id: 'step-read',
        role: 'reader',
        tools: [],
        required_scopes: [],
        matched_profile_id: null,
        jit_hitl: false,
      },
    ]);

    await page.goto(`/autopilot/missions/${mid}`);

    await expect(page.getByTestId('sealed-panel')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('sealed-panel')).toContainText('No scopes required', {
      timeout: 5_000,
    });
  });
});
