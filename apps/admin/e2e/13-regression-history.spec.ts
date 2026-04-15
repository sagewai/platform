/**
 * Regression suite for four admin-panel bugs that were fixed in the
 * same PR as this file landed. Each describe block locks in one fix:
 *
 *   Bug 1 — Playground agents appear on /agents (previously shadowed by
 *           the create_admin_router's own /agents endpoint).
 *   Bug 2 — /playground/run writes an agent_run row, so /admin/runs no
 *           longer returns an empty list after a playground invocation.
 *   Bug 3 — Inline agents inside a /workflows/run execution are recorded
 *           as agent_runs with run_type=workflow_step and a link back to
 *           the parent workflow, so /agents/runs shows per-step rows.
 *   Bug 4 — /workflows/history/<id> renders Stats, Event Log, Cost Flow,
 *           Canvas, and Replay without any PRO/paywall badges or CTA.
 *
 * Each test seeds its own fixture data via the backend API (using the
 * browser's existing auth cookie from storageState) and then asserts
 * against the real UI.
 */
import { test, expect, type Page } from '@playwright/test';

const BACKEND = 'http://localhost:8000';

async function postJson(page: Page, path: string, body: unknown) {
  const res = await page.request.post(`${BACKEND}${path}`, { data: body });
  if (!res.ok()) {
    throw new Error(`POST ${path} → ${res.status()} ${await res.text()}`);
  }
  return res;
}

async function del(page: Page, path: string) {
  await page.request.delete(`${BACKEND}${path}`);
}

// Every /workflows/run call returns its run_id in the workflow_started
// SSE event. Parse it so downstream assertions can check the exact run.
function extractWorkflowRunId(body: string): string {
  const m = body.match(/"run_id":\s*"(wf-[a-f0-9]+)"/);
  if (!m) throw new Error(`no wf run_id found in body: ${body.slice(0, 200)}`);
  return m[1];
}

// Shared workflow YAML for Bug 3 — two sequential inline agents. Uses
// ollama/gemma2:9b because the existing seed fixtures already reference
// it and it doesn't need a network call if litellm isn't set up (the
// error still produces a persisted workflow_step row, which is what
// the regression test checks).
const WORKFLOW_YAML = `name: Regression Pipeline
description: e2e regression
agents:
  alpha:
    model: ollama/gemma2:9b
    system_prompt: one
  beta:
    model: ollama/gemma2:9b
    system_prompt: two
workflow:
  type: sequential
  steps:
    - agent: alpha
    - agent: beta`;

test.describe('Regression — Bug 1: saved playground agent appears on /agents', () => {
  const agentName = `regress-bug1-${Date.now()}`;

  test.afterEach(async ({ page }) => {
    await del(page, `/playground/agents/${agentName}`);
  });

  test('created agent is listed on /agents', async ({ page }) => {
    await postJson(page, '/playground/agent', {
      name: agentName,
      model: 'ollama/gemma2:9b',
      system_prompt: 'hi',
      strategy: 'single',
      temperature: 0.7,
    });

    await page.goto('/agents');
    // The agent name is rendered as a link inside the registry table row.
    await expect(page.getByRole('link', { name: agentName })).toBeVisible({
      timeout: 10_000,
    });
  });
});

test.describe('Regression — Bug 2: /playground/run persists an agent_run', () => {
  const agentName = `regress-bug2-${Date.now()}`;

  test.beforeAll(async ({ request }) => {
    // Create the agent once so /playground/run has something to look up.
    await request.post(`${BACKEND}/playground/agent`, {
      data: {
        name: agentName,
        model: 'ollama/gemma2:9b',
        system_prompt: 'ping',
        strategy: 'single',
        temperature: 0.7,
      },
    });
  });

  test.afterAll(async ({ request }) => {
    await request.delete(`${BACKEND}/playground/agents/${agentName}`);
  });

  test('playground run shows up on /agents/runs', async ({ page }) => {
    // Fire the run. The handler streams SSE — we must drain the response
    // body so the `finally` block flushes the agent_run to disk.
    const runRes = await page.request.post(`${BACKEND}/playground/run`, {
      data: { agent_name: agentName, message: 'hello from regression suite' },
    });
    await runRes.body();

    await page.goto('/agents/runs');

    // Table should now contain a row for this agent tagged standalone.
    const table = page.locator('table');
    await expect(table).toContainText(agentName, { timeout: 10_000 });

    // The agent's row must carry a "standalone" run-type badge. Locate
    // the row by agent name and check for the badge within it.
    const row = page.locator('table tbody tr', { hasText: agentName }).first();
    await expect(row).toContainText(/standalone/i);
  });
});

test.describe('Regression — Bugs 3 & 4: workflow runs record steps and render full history', () => {
  test('inline workflow steps surface as workflow_step rows linked to parent', async ({
    page,
  }) => {
    const res = await postJson(page, '/workflows/run', {
      yaml: WORKFLOW_YAML,
      message: 'kickoff',
    });
    const body = await res.text();
    const workflowRunId = extractWorkflowRunId(body);

    await page.goto('/agents/runs');

    // Both inline agents should appear as rows.
    const table = page.locator('table');
    await expect(table).toContainText('alpha', { timeout: 10_000 });
    await expect(table).toContainText('beta');

    // At least one row shows the "in workflow" badge, not "standalone".
    await expect(page.getByText('in workflow').first()).toBeVisible();

    // Every "in workflow" badge is wrapped in a link pointing at the
    // parent workflow_run_id. Check the first one.
    const firstBadgeLink = page
      .locator('table tbody tr', { hasText: /in workflow/i })
      .first()
      .locator('a', { hasText: /in workflow/i });
    await expect(firstBadgeLink).toHaveAttribute(
      'href',
      `/workflows/history/${workflowRunId}`,
    );
  });

  test('/workflows/history/<id> renders stats, events, and previously-PRO tabs without paywall', async ({
    page,
  }) => {
    const res = await postJson(page, '/workflows/run', {
      yaml: WORKFLOW_YAML,
      message: 'stats test',
    });
    const body = await res.text();
    const workflowRunId = extractWorkflowRunId(body);

    await page.goto(`/workflows/history/${workflowRunId}`);

    // Bug 4b — Stats tab has real numbers (total tokens + agents used).
    await page.getByRole('button', { name: /^Stats/ }).click();
    await expect(page.locator('body')).toContainText(/Total Tokens/);
    await expect(page.locator('body')).toContainText(/Agents Used/);

    // Bug 4a — Event Log replays the stored SSE stream. workflow_started
    // is emitted first, so it must be present after we open the tab.
    await page.getByRole('button', { name: 'Event Log' }).click();
    await expect(page.locator('body')).toContainText(/workflow started/i, {
      timeout: 10_000,
    });

    // Bug 4c — No PRO badge anywhere and no upgrade CTA after clicking
    // through Cost Flow / Canvas / Replay.
    await expect(page.locator('body')).not.toContainText('PRO');
    for (const tabName of ['Cost Flow', 'Canvas', 'Replay']) {
      await page.getByRole('button', { name: tabName }).click();
      await expect(page.locator('body')).not.toContainText('Upgrade');
      await expect(page.locator('body')).not.toContainText('pricing');
    }
  });
});
