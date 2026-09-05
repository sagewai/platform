import { test, expect } from '@playwright/test';

import {
  clarifyingTask,
  mockCoordinatorApi,
  needsYouTask,
  project,
  recordWrites,
  selectProject,
  taskGateDecision,
  taskQuestionDecision,
  workDecision,
  workPending,
} from './coordinator-mocks';

test.describe('Coordinator decisions inbox', () => {
  test('keeps the API order of the inbox', async ({ page }) => {
    const scopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      '/api/v1/tasks/decisions': () => ({
        items: [taskQuestionDecision, taskGateDecision, workDecision],
      }),
    });
    page.on('request', (request) => {
      if (
        request.method() === 'GET' &&
        new URL(request.url()).pathname === '/api/v1/tasks/decisions'
      ) {
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto('/decisions');

    const rows = page.locator('[data-testid^="decision-row-"]');
    await expect(rows).toHaveCount(3);
    await expect(rows.first()).toContainText(taskQuestionDecision.summary);
    await expect(rows.first()).toContainText('today');
    await expect(rows.nth(1)).toContainText(taskGateDecision.summary);
    await expect(rows.last()).toContainText(workDecision.summary);
    expect(scopes).toContain(project.id);
  });

  test('counts Work attention and the inbox Task half on the nav badge', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { '/api/v1/work/pending': () => [workPending] });

    await page.goto('/decisions');

    await expect(
      page.getByRole('button', { name: 'Coordinator, 3 items need attention' }),
    ).toBeVisible();
  });

  test('decides each gate on the route its decided_by names', async ({ page }) => {
    const writes: string[] = [];
    const bodies: unknown[] = [];
    const scopes: Array<string | undefined> = [];
    const workGatePath = '/api/v1/work/work-9/gates/merge:work-9:3';
    const taskGatePath = `/api/v1/tasks/${needsYouTask.task_id}/gates/plan:${needsYouTask.task_id}:1`;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [workGatePath]: () => ({
        work_id: 'work-9',
        gate_id: 'merge:work-9:3',
        decision: 'allow',
      }),
      [taskGatePath]: () => needsYouTask,
    });
    recordWrites(page, writes);
    page.on('request', (request) => {
      const pathname = decodeURIComponent(new URL(request.url()).pathname);
      if (request.method() !== 'POST' || (pathname !== workGatePath && pathname !== taskGatePath)) {
        return;
      }
      bodies.push(JSON.parse(request.postData() ?? '{}'));
      scopes.push(request.headers()['x-project-id']);
    });

    await page.goto('/decisions');
    await page
      .getByTestId(`decision-row-${workDecision.attention_id}`)
      .getByRole('button', { name: 'Allow' })
      .click();
    await page
      .getByTestId(`decision-row-${taskGateDecision.attention_id}`)
      .getByRole('button', { name: 'Allow' })
      .click();

    await expect.poll(() => writes).toEqual([workGatePath, taskGatePath]);
    expect(bodies).toEqual([{ decision: 'allow' }, { decision: 'allow', note: null }]);
    expect(scopes).toEqual([project.id, project.id]);
  });

  test('answers a clarification in place, at the version the item carries', async ({ page }) => {
    const bodies: unknown[] = [];
    const scopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${taskQuestionDecision.task_id}/answers`]: () => clarifyingTask,
    });
    page.on('request', (request) => {
      if (!request.url().endsWith('/answers')) return;
      bodies.push(JSON.parse(request.postData() ?? '{}'));
      scopes.push(request.headers()['x-project-id']);
    });

    await page.goto('/decisions');
    const row = page.getByTestId(`decision-row-${taskQuestionDecision.attention_id}`);
    await expect(row.getByRole('button', { name: 'Send answer' })).toBeVisible();
    await expect(row.getByRole('button', { name: 'Use default' })).toHaveCount(0);
    await row.getByLabel(`Answer to ${taskQuestionDecision.attention_id}`).fill('main');
    await row.getByRole('button', { name: 'Send answer' }).click();

    await expect.poll(() => bodies).toEqual([
      { attention_id: 'q-scope', attention_version: 2, answer: 'main' },
    ]);
    expect(scopes).toEqual([project.id]);
  });

  test('shows the inbox refusal without claiming Work attention failed', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { '/api/v1/work/pending': () => [workPending] });
    await page.route(
      (url) => url.pathname === '/api/v1/tasks/decisions',
      async (route) => {
        await route.fulfill({ status: 503, json: { detail: 'inbox unavailable' } });
      },
    );

    await page.goto('/decisions');

    await expect(page.getByTestId('decisions-error')).toHaveText('inbox unavailable');
    await expect(
      page.getByRole('button', { name: 'Coordinator, 1 items need attention' }),
    ).toBeVisible();

    await page.goto('/work');

    await expect(page.getByRole('heading', { name: 'Needs Attention' })).toBeVisible();
    await expect(page.getByText('Failed to load pending Work attention.')).toHaveCount(0);
  });

  test('re-reads the inbox after a decision', async ({ page }) => {
    let decided = false;
    let reads = 0;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      '/api/v1/tasks/decisions': () => {
        reads += 1;
        return {
          items: decided
            ? [taskGateDecision, taskQuestionDecision]
            : [workDecision, taskGateDecision, taskQuestionDecision],
        };
      },
      '/api/v1/work/work-9/gates/merge:work-9:3': () => {
        decided = true;
        return { work_id: 'work-9', gate_id: 'merge:work-9:3', decision: 'allow' };
      },
    });

    await page.goto('/decisions');
    await expect(page.locator('[data-testid^="decision-row-"]')).toHaveCount(3);
    const before = reads;
    await page
      .getByTestId(`decision-row-${workDecision.attention_id}`)
      .getByRole('button', { name: 'Allow' })
      .click();

    await expect(page.getByTestId(`decision-row-${workDecision.attention_id}`)).toHaveCount(0);
    expect(reads).toBeGreaterThan(before);
  });

  test('shows the empty state when nothing is owed', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { '/api/v1/tasks/decisions': () => ({ items: [] }) });

    await page.goto('/decisions');

    await expect(page.getByRole('heading', { name: 'Nothing needs you' })).toBeVisible();
  });

  test('asks for a project before mounting the inbox', async ({ page }) => {
    const decisionReads: string[] = [];
    await mockCoordinatorApi(page);
    await page.route('**/api/v1/projects', async (route) => {
      await route.fulfill({ json: [] });
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/v1/tasks/decisions') decisionReads.push(url.pathname);
    });

    await page.goto('/decisions');

    await expect(page.getByRole('heading', { name: 'Select a project' })).toBeVisible();
    await expect(page.locator('[data-testid^="decision-row-"]')).toHaveCount(0);
    expect(decisionReads).toEqual([]);
  });
});
