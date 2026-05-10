import { test, expect, type Page } from '@playwright/test';

/**
 * E2E for Plan G — Autopilot mission detail page.
 *
 * Strategy: mock the two new backend endpoints with `page.route` so the test
 * is independent of the hosted blueprint service (which is gated). The list
 * endpoint is also mocked so the row-click navigation can be exercised.
 */

interface MockMissionOpts {
  missionId: string;
  goalText?: string;
  agentGraph: {
    nodes: Array<{
      id: string;
      role: string;
      kind: 'llm' | 'deterministic';
      tools: string[];
      prompt_ref: string | null;
    }>;
    edges: Array<{ from: string; to: string; label?: string }>;
  };
  toolsRequired?: Array<{ name: string; description?: string }>;
  providersRequired?: Array<{ name: string; tier?: string }>;
  estimatedCost?: { currency: string; amount: number } | null;
  description?: string;
}

async function mockMissionDetail(page: Page, opts: MockMissionOpts) {
  const detail = {
    id: opts.missionId,
    mission_id: opts.missionId,
    status: 'pending',
    goal_text: opts.goalText ?? 'Summarize fisheries papers',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    project_id: null,
    blueprint_id: 'bp-test',
    description: opts.description ?? 'Summarize a corpus of papers.',
    agent_graph_json: opts.agentGraph,
    tools_required: opts.toolsRequired ?? [],
    providers_required: opts.providersRequired ?? [],
    slots: { topic: 'fisheries' },
    success_criteria: [{ metric: 'accuracy', op: '>=', target: 0.9 }],
    training_data_hooks: [],
    // Use !== undefined so explicit null is preserved (null → licensing link shown).
    estimated_cost: opts.estimatedCost !== undefined ? opts.estimatedCost : { currency: 'USD', amount: 0.42 },
  };

  // Register catch-all FIRST (lowest LIFO priority) so sandbox/fleet/sealed sub-routes
  // return [] without crashing, and specific mocks registered after take precedence.
  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${opts.missionId}/`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      }),
  );
  await page.route(/\/api\/v1\/autopilot\/fleet\/workers/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
  );

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${opts.missionId}$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(detail),
      }),
  );

  const explainBody = {
    markdown: [
      '## What this will do',
      detail.description,
      '## Resources allocated',
      '- web_search',
      '## How to run',
      'Click the **Run mission** button at the top of this page.',
      '## How to debug',
      'View the live trace once Plan H lands.',
    ].join('\n\n'),
    sections: {
      what_it_does: detail.description,
      resources: '- web_search',
      how_to_run: 'Click run.',
      how_to_debug: 'View trace.',
    },
  };

  await page.route(
    new RegExp(`/api/v1/autopilot/missions/${opts.missionId}/explain$`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(explainBody),
      }),
  );

  return { detail, explain: explainBody };
}

async function mockMissionList(
  page: Page,
  missions: Array<{ id: string; goal: string }>,
) {
  await page.route(/\/api\/v1\/autopilot\/missions(\?.*)?$/, (route) => {
    const url = route.request().url();
    // Don't intercept the mission-detail or explain or events sub-paths.
    if (/\/missions\/[^/?]+(\/|$)/.test(url)) return route.fallback();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        missions: missions.map((m) => ({
          id: m.id,
          mission_id: m.id,
          blueprint_title: m.goal,
          blueprint_category: 'research',
          status: 'pending',
          mode: 'batch',
          project_id: null,
          started_at: null,
          finished_at: null,
          steps: [],
        })),
        count: missions.length,
        total: missions.length,
      }),
    });
  });
}

test.describe('autopilot mission detail page', () => {
  test('renders agent graph + brief from mocked detail endpoint', async ({
    page,
  }) => {
    await mockMissionDetail(page, {
      missionId: 'm-42',
      agentGraph: {
        nodes: [
          {
            id: 'a',
            role: 'planner',
            kind: 'llm',
            tools: ['web_search'],
            prompt_ref: 'prompts/planner.md',
          },
          {
            id: 'b',
            role: 'writer',
            kind: 'llm',
            tools: [],
            prompt_ref: 'prompts/writer.md',
          },
        ],
        edges: [{ from: 'a', to: 'b' }],
      },
      toolsRequired: [{ name: 'web_search', description: 'search the web' }],
      providersRequired: [{ name: 'primary', tier: 'medium' }],
    });

    await page.goto('/autopilot/missions/m-42');

    // Header — goal + cost + Run-mission button (enabled — Plan H wired it up)
    await expect(
      page.getByRole('heading', { name: /summarize fisheries/i }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: /run mission/i }),
    ).toBeEnabled();
    await expect(page.getByText(/\$0\.42/)).toBeVisible();

    // Agent graph — 2 custom nodes, both labels visible
    await expect(page.getByTestId('agent-graph')).toBeVisible();
    await expect(page.getByTestId('agent-graph-node')).toHaveCount(2);
    await expect(page.getByText('planner', { exact: true })).toBeVisible();
    await expect(page.getByText('writer', { exact: true })).toBeVisible();

    // Resource panels — tool / provider / slot value
    await expect(page.getByTestId('resource-panel-tools')).toContainText(
      'web_search',
    );
    await expect(page.getByTestId('resource-panel-providers')).toContainText(
      'primary',
    );
    await expect(page.getByTestId('resource-panel-slots')).toContainText(
      'topic',
    );

    // Directions brief — H2 from the markdown
    await expect(
      page.getByRole('heading', { name: /what this will do/i }),
    ).toBeVisible();
  });

  test('falls back to licensing link when estimated_cost is null', async ({
    page,
  }) => {
    await mockMissionDetail(page, {
      missionId: 'm-cost',
      agentGraph: { nodes: [], edges: [] },
      estimatedCost: null,
    });

    await page.goto('/autopilot/missions/m-cost');
    await expect(page.getByTestId('mission-licensing-link')).toBeVisible();
  });

  test('shows not-found UI for an unknown mission id', async ({ page }) => {
    await page.route(
      /\/api\/v1\/autopilot\/missions\/does-not-exist$/,
      (route) =>
        route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ detail: "Mission 'does-not-exist' not found" }),
        }),
    );
    await page.goto('/autopilot/missions/does-not-exist');
    await expect(
      page.getByRole('heading', { name: /mission not found/i }),
    ).toBeVisible();
  });

  test('row click on /autopilot/missions navigates to detail', async ({
    page,
  }) => {
    // First the detail endpoint so navigation lands on a renderable page.
    await mockMissionDetail(page, {
      missionId: 'm-list-click',
      agentGraph: { nodes: [], edges: [] },
    });
    await mockMissionList(page, [
      { id: 'm-list-click', goal: 'Summarize fisheries papers' },
    ]);

    await page.goto('/autopilot/missions');
    const row = page.getByTestId('mission-row-m-list-click');
    await expect(row).toBeVisible();
    await row.click();
    await expect(page).toHaveURL(/\/autopilot\/missions\/m-list-click$/);
  });

  test('chevron click toggles expansion without navigating', async ({
    page,
  }) => {
    await mockMissionList(page, [
      { id: 'm-chevron', goal: 'Pipeline test' },
    ]);

    await page.goto('/autopilot/missions');
    const chevron = page.getByTestId('mission-row-chevron-m-chevron');
    await expect(chevron).toBeVisible();
    await chevron.click();
    // Stayed on the same page.
    await expect(page).toHaveURL(/\/autopilot\/missions$/);
    // Expanded sub-row content visible.
    await expect(page.getByText(/no step results yet/i)).toBeVisible();
  });
});
