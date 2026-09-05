import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';

import {
  acceptedPlanDetail,
  activityFirstPage,
  activitySecondPage,
  answeredThread,
  briefBody,
  deliverAction,
  failedMergeAction,
  mergeAction,
  mirroredGateTask,
  mirroredGateThread,
  mockCoordinatorApi,
  mockTaskStream,
  needsYouTask,
  project,
  recordWrites,
  scheduledTask,
  scheduledTaskDetail,
  scheduledTelemetry,
  selectProject,
  settledGateThread,
  sseBody,
  task as makeTask,
  taskBudget,
  taskDetail,
  taskDetailTask as task,
  taskPlan,
  telemetry,
  thread,
  trigger,
} from './coordinator-mocks';
import type { TaskDetail, TaskTelemetry, TaskTriggerSpec } from '../utils/types';

test.describe('Coordinator Task page', () => {
  test('shows the header, the status and the six tabs', async ({ page }) => {
    const detailScopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}/thread`]: () => ({
        task_id: task.id,
        project_id: task.project_id,
        brief_ref: null,
        entries: [],
        open_question_ids: [],
        pending_gate: null,
      }),
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/tasks/${task.id}`) {
        detailScopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}`);

    await expect(page.getByRole('heading', { name: needsYouTask.title })).toBeVisible();
    await expect(page.getByTestId('task-status')).toHaveText('PLAN PROPOSED');
    expect(detailScopes).toContain(project.id);
    await expect(page.getByRole('link', { name: 'Tracking issue' })).toHaveAttribute(
      'href',
      'https://github.com/sagewai/platform/issues/42',
    );
    const tabs = page.getByRole('navigation', { name: 'Task views' });
    await expect(tabs.getByRole('link', { name: 'Thread' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    for (const [label, href] of [
      ['Plan', `/tasks/${task.id}/plan`],
      ['Actions', `/tasks/${task.id}/actions`],
      ['Activity', `/tasks/${task.id}/activity`],
      ['Telemetry', `/tasks/${task.id}/telemetry`],
      ['Settings', `/tasks/${task.id}/settings`],
    ]) {
      await expect(tabs.getByRole('link', { name: label })).toHaveAttribute('href', href);
    }
  });

  test('pauses, resumes and cancels through the lifecycle routes', async ({ page }) => {
    const writes: string[] = [];
    const cancelBodies: Array<string | null> = [];
    const pauseScopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}/thread`]: () => ({
        task_id: task.id,
        project_id: task.project_id,
        brief_ref: null,
        entries: [],
        open_question_ids: [],
        pending_gate: null,
      }),
      [`/api/v1/tasks/${task.id}/pause`]: () => ({ ...needsYouTask, status: 'PAUSED' }),
      [`/api/v1/tasks/${task.id}/resume`]: () => ({ ...needsYouTask, status: 'PLAN_PROPOSED' }),
      [`/api/v1/tasks/${task.id}/cancel`]: () => ({
        ...needsYouTask,
        status: 'CANCELLED',
        board_column: 'done',
      }),
    });
    recordWrites(page, writes);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'POST' && url.pathname === `/api/v1/tasks/${task.id}/pause`) {
        pauseScopes.push(request.headers()['x-project-id']);
      }
      if (request.method() === 'POST' && url.pathname === `/api/v1/tasks/${task.id}/cancel`) {
        cancelBodies.push(request.postData());
      }
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByRole('button', { name: 'Pause' }).evaluate((button) => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await expect(page.getByTestId('task-status')).toHaveText('PAUSED');

    await page.getByRole('button', { name: 'Resume' }).click();
    await expect(page.getByTestId('task-status')).toHaveText('PLAN PROPOSED');

    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByTestId('task-status')).toHaveText('CANCELLED');
    expect(writes).toEqual([
      `/api/v1/tasks/${task.id}/pause`,
      `/api/v1/tasks/${task.id}/resume`,
      `/api/v1/tasks/${task.id}/cancel`,
    ]);
    expect(pauseScopes).toEqual([project.id]);
    expect(cancelBodies).toEqual(['{"note":null}']);
  });

  test('re-reads the detail when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let feedDelivered = false;
    let detailResponses = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => {
        detailResponses += 1;
        return {
          ...taskDetail,
          record: feedDelivered
            ? { ...needsYouTask, status: 'EXECUTING', board_column: 'in_progress' }
            : needsYouTask,
        } satisfies TaskDetail;
      },
      [`/api/v1/tasks/${task.id}/thread`]: () => ({
        task_id: task.id,
        project_id: task.project_id,
        brief_ref: null,
        entries: [],
        open_question_ids: [],
        pending_gate: null,
      }),
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_STATUS_CHANGED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}`);
    await expect(page.getByTestId('task-status')).toHaveText('PLAN PROPOSED');
    feedDelivered = true;
    releaseStream();

    await expect(page.getByTestId('task-status')).toHaveText('EXECUTING');
    expect(detailResponses).toBeGreaterThan(1);
  });

  test('surfaces a lost race as the API stated it', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}/thread`]: () => ({
        task_id: task.id,
        project_id: task.project_id,
        brief_ref: null,
        entries: [],
        open_question_ids: [],
        pending_gate: null,
      }),
    });
    await page.route(`**/api/v1/tasks/${task.id}/pause`, async (route) => {
      await route.fulfill({ status: 409, json: { detail: 'projection changed under the append' } });
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByRole('button', { name: 'Pause' }).click();

    await expect(page.getByTestId('task-error')).toHaveText(
      'projection changed under the append',
    );
  });

  test('renders the not-found state for an unknown Task', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route('**/api/v1/tasks/task-missing**', async (route) => {
      await route.fulfill({ status: 404, json: { detail: 'Not found' } });
    });

    await page.goto('/tasks/task-missing');

    await expect(page.getByRole('heading', { name: 'Task not found' })).toBeVisible();
    await expect(page.getByText('Not found', { exact: true })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Task views' })).toHaveCount(0);
    await expect(page.getByTestId('task-error')).toHaveCount(0);
  });

  test('renders the thread in stream order', async ({ page }) => {
    const threadScopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/tasks/${task.id}/thread`) {
        threadScopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}`);

    await expect(page.getByTestId('thread-entry-2')).toContainText(
      'Approve the weekly report delivery',
    );
    await expect(page.getByTestId('thread-entry-3')).toContainText('Planning the change.');
    await expect(page.getByTestId('thread-entry-4:q-scope')).toContainText(
      'Which branch should the change land on?',
    );
    await expect(page.getByTestId('thread-entry-5')).toContainText(
      'Approve the plan at version 1?',
    );
    await expect(page.getByTestId('thread-entry-6')).toContainText('PLAN_PROPOSED');
    await expect(page.getByTestId('thread-entry-3')).toContainText('coordinator');
    expect(threadScopes).toContain(project.id);
  });

  test('surfaces a thread read failure as the API stated it', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(`**/api/v1/tasks/${task.id}/thread`, async (route) => {
      await route.fulfill({ status: 503, json: { detail: 'thread projection unavailable' } });
    });

    await page.goto(`/tasks/${task.id}`);

    await expect(page.getByTestId('task-thread-error')).toHaveText(
      'thread projection unavailable',
    );
  });

  test('re-reads the thread when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let threadRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}/thread`]: () => {
        threadRequests += 1;
        return thread;
      },
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_STATUS_CHANGED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}`);
    await expect(page.getByTestId('thread-entry-2')).toContainText(
      'Approve the weekly report delivery',
    );
    threadRequests = 1;
    releaseStream();

    await expect.poll(() => threadRequests).toBeGreaterThan(1);
  });

  test('opens the brief artifact through the artifact route', async ({ page }) => {
    const artifactRequests: Array<{ scope: string | undefined; url: URL }> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/v1/artifacts/')) {
        artifactRequests.push({ scope: request.headers()['x-project-id'], url });
      }
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByTestId('artifact-open-brief').click();

    await expect(page.getByTestId('artifact-body-brief')).toContainText(
      'Deliver the weekly report',
    );
    expect(artifactRequests).toHaveLength(1);
    expect(artifactRequests[0].scope).toBe(project.id);
    expect(artifactRequests[0].url.searchParams.get('task_id')).toBe(task.id);
  });

  test('refuses to render a restricted artifact', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    await page.route('**/api/v1/artifacts/**', async (route) => {
      await route.fulfill({
        status: 403,
        json: { detail: 'restricted content never leaves the console sink' },
      });
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByTestId('artifact-open-brief').click();

    await expect(page.getByTestId('artifact-error-brief')).toHaveText(
      'Restricted content never leaves the console sink.',
    );
  });

  test('answers a question at the version the entry carries', async ({ page }) => {
    const bodies: unknown[] = [];
    const scopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    page.on('request', (request) => {
      if (request.url().endsWith('/answers')) {
        bodies.push(JSON.parse(request.postData() ?? '{}'));
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByLabel('Answer to q-scope').fill('main');
    await page.getByRole('button', { name: 'Send answer' }).click();

    await expect.poll(() => bodies).toEqual([
      { attention_id: 'q-scope', attention_version: 2, answer: 'main' },
    ]);
    expect(scopes).toEqual([project.id]);
  });

  test('takes the default instead of typing one', async ({ page }) => {
    const bodies: unknown[] = [];
    const scopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    page.on('request', (request) => {
      if (request.url().endsWith('/answers')) {
        bodies.push(JSON.parse(request.postData() ?? '{}'));
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByRole('button', { name: 'Use default' }).click();

    await expect.poll(() => bodies).toEqual([
      { attention_id: 'q-scope', attention_version: 2, use_default: true },
    ]);
    expect(scopes).toEqual([project.id]);
  });

  test('renders a settled question as settled, with who settled it', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => answeredThread });
    await mockTaskStream(page);

    await page.goto(`/tasks/${task.id}`);

    const entry = page.getByTestId('thread-entry-4:q-scope');
    await expect(entry).toContainText('Defaulted: main');
    await expect(entry.getByRole('button', { name: 'Send answer' })).toHaveCount(0);
    await expect(entry.getByRole('button', { name: 'Use default' })).toHaveCount(0);
  });

  test('shows the refusal when the answer loses its version fence', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    await page.route(
      (url) => url.pathname.endsWith('/answers'),
      async (route) => {
        await route.fulfill({
          status: 409,
          json: { detail: 'question q-scope is at attention version 3' },
        });
      },
    );

    await page.goto(`/tasks/${task.id}`);
    await page.getByRole('button', { name: 'Use default' }).click();

    await expect(page.getByTestId('answer-error')).toHaveText(
      'question q-scope is at attention version 3',
    );
  });

  test('decides a decided_by task gate on the Task route', async ({ page }) => {
    const bodies: unknown[] = [];
    const scopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (
        request.method() === 'POST' &&
        decodeURIComponent(url.pathname) === `/api/v1/tasks/${task.id}/gates/plan:${task.id}:1`
      ) {
        bodies.push(JSON.parse(request.postData() ?? '{}'));
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByLabel('Note for plan:task-2:1').fill('looks right');
    await page
      .getByTestId('gate-controls-plan:task-2:1')
      .getByRole('button', { name: 'Allow' })
      .click();

    await expect.poll(() => bodies).toEqual([{ decision: 'allow', note: 'looks right' }]);
    expect(scopes).toEqual([project.id]);
  });

  test('decides a decided_by work gate on the Work route', async ({ page }) => {
    const bodies: unknown[] = [];
    const scopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${mirroredGateTask.task_id}`]: () =>
        ({
          ...taskDetail,
          task: makeTask(mirroredGateTask),
          record: mirroredGateTask,
        }) satisfies TaskDetail,
      [`/api/v1/tasks/${mirroredGateTask.task_id}/thread`]: () => mirroredGateThread,
      [`/api/v1/tasks/${mirroredGateTask.task_id}/events`]: () => '',
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (
        request.method() === 'POST' &&
        decodeURIComponent(url.pathname) === '/api/v1/work/work-9/gates/merge:work-9:3'
      ) {
        bodies.push(JSON.parse(request.postData() ?? '{}'));
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${mirroredGateTask.task_id}`);
    await page
      .getByTestId('gate-controls-merge:work-9:3')
      .getByRole('button', { name: 'Allow' })
      .click();

    await expect.poll(() => bodies).toEqual([{ decision: 'allow' }]);
    expect(scopes).toEqual([project.id]);
  });

  test('sends a message once under an Idempotency-Key', async ({ page }) => {
    const requests: Array<{
      body: unknown;
      key: string | undefined;
      scope: string | undefined;
    }> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    page.on('request', (request) => {
      if (request.url().endsWith('/messages')) {
        requests.push({
          body: JSON.parse(request.postData() ?? '{}'),
          key: request.headers()['idempotency-key'],
          scope: request.headers()['x-project-id'],
        });
      }
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByLabel('Message').fill('Deploying after the gate.');
    await page.getByRole('button', { name: 'Send message' }).click();

    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].body).toEqual({ text: 'Deploying after the gate.' });
    expect(requests[0].key).toMatch(/^[0-9a-f]{32}$/);
    expect(requests[0].scope).toBe(project.id);
    await expect(page.getByLabel('Message')).toHaveValue('');
  });

  test('reuses the Idempotency-Key when the first send fails', async ({ page }) => {
    const requests: Array<{
      body: unknown;
      key: string | undefined;
      scope: string | undefined;
    }> = [];
    let attempts = 0;
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    await page.route(`**/api/v1/tasks/${task.id}/messages`, async (route) => {
      requests.push({
        body: JSON.parse(route.request().postData() ?? '{}'),
        key: route.request().headers()['idempotency-key'],
        scope: route.request().headers()['x-project-id'],
      });
      attempts += 1;
      if (attempts === 1) {
        await route.fulfill({
          status: 409,
          json: { detail: 'projection changed under the append' },
        });
        return;
      }
      await route.fulfill({ json: needsYouTask });
    });

    await page.goto(`/tasks/${task.id}`);
    await page.getByLabel('Message').fill('Deploying after the gate.');
    await page.getByRole('button', { name: 'Send message' }).click();
    await expect(page.getByTestId('message-error')).toHaveText(
      'projection changed under the append',
    );

    await page.getByRole('button', { name: 'Send message' }).click();

    await expect.poll(() => requests.length).toBe(2);
    expect(requests.map((request) => request.body)).toEqual([
      { text: 'Deploying after the gate.' },
      { text: 'Deploying after the gate.' },
    ]);
    expect(requests[1].key).toMatch(/^[0-9a-f]{32}$/);
    expect(requests[0].key).toBe(requests[1].key);
    expect(requests.map((request) => request.scope)).toEqual([project.id, project.id]);
  });

  test('resumes the feed with Last-Event-ID after the stream ends', async ({ page }) => {
    const resumeIds: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await page.route('**/api/v1/artifacts/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/markdown', body: briefBody });
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      resumeIds.push(route.request().headers()['last-event-id']);
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `${sseBody()}id: 9\n`,
      });
    });

    await page.goto(`/tasks/${task.id}`);

    await expect.poll(() => resumeIds.at(-1), { timeout: 15_000 }).toBe('4');
    expect(resumeIds.length).toBeGreaterThan(1);
    expect(resumeIds[0]).toBeUndefined();
  });

  test('offers no controls for a gate the console cannot decide', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${mirroredGateTask.task_id}`]: () =>
        ({
          ...taskDetail,
          task: makeTask(mirroredGateTask),
          record: mirroredGateTask,
        }) satisfies TaskDetail,
      [`/api/v1/tasks/${mirroredGateTask.task_id}/thread`]: () => settledGateThread,
      [`/api/v1/tasks/${mirroredGateTask.task_id}/events`]: () => '',
    });

    await page.goto(`/tasks/${mirroredGateTask.task_id}`);

    await expect(page.getByTestId('thread-entry-7')).toContainText(
      'decide with sagewai work approve',
    );
    await expect(page.getByTestId('thread-entry-8')).toContainText('Decided: allow');
    await expect(page.getByTestId('gate-controls-deploy_production:work-9:1')).toHaveCount(0);
    await expect(
      page.getByTestId(`gate-controls-deliver:${mirroredGateTask.task_id}:1`),
    ).toHaveCount(0);
    await expect(page.getByTestId(`gate-controls-plan:${mirroredGateTask.task_id}:1`)).toHaveCount(
      0,
    );
  });

  test('shows the gate refusal the API stated', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    await page.route(
      (url) => url.pathname.includes('/gates/'),
      async (route) => {
        await route.fulfill({
          status: 409,
          json: { detail: `gate is not pending: plan:${task.id}:1` },
        });
      },
    );

    await page.goto(`/tasks/${task.id}`);
    await page
      .getByTestId(`gate-controls-plan:${task.id}:1`)
      .getByRole('button', { name: 'Allow' })
      .click();

    await expect(page.getByTestId('gate-error')).toHaveText(
      `gate is not pending: plan:${task.id}:1`,
    );
  });

  test('keeps the message draft when the feed advances', async ({ page }) => {
    let reads = 0;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}/thread`]: () => {
        reads += 1;
        return thread;
      },
    });
    await mockTaskStream(page);

    await page.goto(`/tasks/${task.id}`);
    await page.getByLabel('Message').fill('Half-typed.');
    const before = reads;
    await expect.poll(() => reads, { timeout: 10_000 }).toBeGreaterThan(before);

    await expect(page.getByLabel('Message')).toHaveValue('Half-typed.');
  });

  test('renders the accepted plan as a checklist with its acceptance matrix', async ({ page }) => {
    const detailScopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/tasks/${task.id}`) {
        detailScopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}/plan`);

    await expect(page.getByRole('heading', { name: 'Plan version 1' })).toBeVisible();
    const first = page.getByTestId('plan-step-step-1');
    await expect(first).toContainText('Add the coordinator board');
    await expect(first).toContainText('Render the five columns from the board route.');
    await expect(first).toContainText('The board renders five columns.');
    await expect(first).toContainText('apps/admin/app/board');
    await expect(first).toContainText('risk low');
    const second = page.getByTestId('plan-step-step-2');
    await expect(second).toContainText('after step-1');
    const matrix = page.getByTestId('acceptance-matrix');
    await expect(matrix).toContainText(
      'pnpm --filter @sagewai/admin exec playwright test 16-coordinator',
    );
    await expect(matrix).toContainText('assessment');
    expect(detailScopes).toContain(project.id);
  });

  test('says so when no plan is accepted yet', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto(`/tasks/${task.id}/plan`);

    await expect(page.getByRole('heading', { name: 'No accepted plan' })).toBeVisible();
  });

  test('shows the plan refusal the API stated', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto(`/tasks/${task.id}`);
    await expect(page.getByRole('heading', { name: needsYouTask.title })).toBeVisible();

    await page.route(`**/api/v1/tasks/${task.id}`, async (route) => {
      await route.fulfill({ status: 503, json: { detail: 'plan projection unavailable' } });
    });
    await page.getByRole('link', { name: 'Plan' }).click();

    await expect(page.getByTestId('task-plan-error')).toHaveText('plan projection unavailable');
  });

  test('re-reads the accepted plan when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let feedDelivered = false;
    let detailResponses = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => {
        detailResponses += 1;
        return feedDelivered
          ? {
              ...acceptedPlanDetail,
              record: { ...acceptedPlanDetail.record, plan_version: 2 },
              plan: { ...taskPlan, version: 2 },
            }
          : acceptedPlanDetail;
      },
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_PLAN_ACCEPTED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/plan`);
    await expect(page.getByRole('heading', { name: 'Plan version 1' })).toBeVisible();
    const before = detailResponses;
    feedDelivered = true;
    releaseStream();

    await expect(page.getByRole('heading', { name: 'Plan version 2' })).toBeVisible();
    expect(detailResponses).toBeGreaterThan(before);
  });

  test('keeps the plan and states the refusal when a refetch fails', async ({ page }) => {
    let releaseStream = () => {};
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_PLAN_ACCEPTED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/plan`);
    await expect(page.getByTestId('plan-step-step-1')).toBeVisible();

    await page.route(`**/api/v1/tasks/${task.id}`, async (route) => {
      await route.fulfill({ status: 503, json: { detail: 'plan projection unavailable' } });
    });
    releaseStream();

    await expect(page.getByTestId('task-plan-error')).toHaveText('plan projection unavailable');
    await expect(page.getByTestId('plan-step-step-1')).toBeVisible();
  });

  test('keeps the API order of the plan steps', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () =>
        ({
          ...acceptedPlanDetail,
          plan: { ...taskPlan, steps: [...taskPlan.steps].reverse() },
        }) satisfies TaskDetail,
    });

    await page.goto(`/tasks/${task.id}/plan`);

    const steps = page.locator('[data-testid^="plan-step-"]');
    await expect(steps.first()).toContainText('Wire the decisions inbox');
    await expect(steps.last()).toContainText('Add the coordinator board');
  });

  test('lists the action records with their reversibility and post-check', async ({ page }) => {
    const actionScopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/tasks/${task.id}/actions`) {
        actionScopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}/actions`);

    const merge = page.getByTestId('action-row-merge:work-9:3');
    await expect(merge).toContainText('merge_pull_request');
    await expect(merge).toContainText('compensatable');
    await expect(merge).toContainText('merged_sha_read_back');
    await expect(merge).toContainText('passed');
    const deliver = page.getByTestId('action-row-deliver:work-10:1');
    await expect(deliver).toContainText('irreversible');
    await expect(deliver).toContainText('the sink refused the comment');
    await expect(deliver.getByRole('button', { name: 'Request rollback' })).toHaveCount(0);
    await expect(
      page
        .getByTestId('action-row-merge:work-9:4')
        .getByRole('button', { name: 'Request rollback' }),
    ).toHaveCount(0);
    expect(actionScopes).toContain(project.id);
  });

  test('says so when there are no recorded actions yet', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/actions`]: () => ({ actions: [] }),
    });

    await page.goto(`/tasks/${task.id}/actions`);

    await expect(page.getByRole('heading', { name: 'No actions yet' })).toBeVisible();
  });

  test('shows the actions refusal the API stated', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });

    await page.goto(`/tasks/${task.id}`);
    await expect(page.getByRole('heading', { name: needsYouTask.title })).toBeVisible();

    await page.route(`**/api/v1/tasks/${task.id}/actions`, async (route) => {
      await route.fulfill({ status: 503, json: { detail: 'actions projection unavailable' } });
    });
    await page.getByRole('link', { name: 'Actions' }).click();

    await expect(page.getByTestId('task-actions-error')).toHaveText(
      'actions projection unavailable',
    );
  });

  test('re-reads the actions when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let actionRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/actions`]: () => {
        actionRequests += 1;
        return { actions: [mergeAction, deliverAction, failedMergeAction] };
      },
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'ACTION_RESULT_RECORDED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/actions`);
    await expect(page.getByTestId('action-row-merge:work-9:3')).toBeVisible();
    const before = actionRequests;
    releaseStream();

    await expect.poll(() => actionRequests).toBeGreaterThan(before);
  });

  test('keeps the actions and states the refusal when a refetch fails', async ({ page }) => {
    let releaseStream = () => {};
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'ACTION_RESULT_RECORDED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/actions`);
    await expect(page.getByTestId('action-row-merge:work-9:3')).toBeVisible();

    await page.route(`**/api/v1/tasks/${task.id}/actions`, async (route) => {
      await route.fulfill({ status: 503, json: { detail: 'actions projection unavailable' } });
    });
    releaseStream();

    await expect(page.getByTestId('task-actions-error')).toHaveText(
      'actions projection unavailable',
    );
    await expect(page.getByTestId('action-row-merge:work-9:3')).toBeVisible();
  });

  test('keeps the API order of the action records', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/actions`]: () => ({
        actions: [failedMergeAction, deliverAction, mergeAction],
      }),
    });

    await page.goto(`/tasks/${task.id}/actions`);

    const rows = page.locator('[data-testid^="action-row-"]');
    await expect(rows.first()).toContainText('the merge was rejected');
    await expect(rows.last()).toContainText('https://github.com/sagewai/platform/pull/3');
  });

  test('requests the recorded rollback for a compensatable action', async ({ page }) => {
    const writes: string[] = [];
    const bodies: Array<string | null> = [];
    const scopes: Array<string | undefined> = [];
    let actionReads = 0;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/actions`]: () => {
        actionReads += 1;
        return { actions: [mergeAction, deliverAction, failedMergeAction] };
      },
    });
    recordWrites(page, writes);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (
        request.method() === 'POST' &&
        decodeURIComponent(url.pathname) ===
          `/api/v1/tasks/${task.id}/actions/${mergeAction.action_id}/rollback`
      ) {
        bodies.push(request.postData());
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}/actions`);
    await expect(page.getByTestId('action-row-merge:work-9:3')).toBeVisible();
    const before = actionReads;
    await page
      .getByTestId('action-row-merge:work-9:3')
      .getByRole('button', { name: 'Request rollback' })
      .click();

    await expect(
      page.locator('[data-sonner-toast]').filter({ hasText: 'Rollback requested' }),
    ).toBeVisible();
    await expect.poll(() => writes).toEqual([
      `/api/v1/tasks/${task.id}/actions/${mergeAction.action_id}/rollback`,
    ]);
    expect(bodies).toEqual(['{}']);
    expect(scopes).toEqual([project.id]);
    await expect.poll(() => actionReads).toBeGreaterThan(before);
  });

  test('shows the rollback in flight and sends it once', async ({ page }) => {
    const writes: string[] = [];
    let releaseRollback = () => {};
    const rollbackReady = new Promise<void>((resolve) => {
      releaseRollback = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    recordWrites(page, writes);
    await page.route(
      (url) =>
        decodeURIComponent(url.pathname) ===
        `/api/v1/tasks/${task.id}/actions/${mergeAction.action_id}/rollback`,
      async (route) => {
        await rollbackReady;
        await route.fulfill({ json: acceptedPlanDetail.record });
      },
    );

    await page.goto(`/tasks/${task.id}/actions`);
    await page
      .getByTestId('action-row-merge:work-9:3')
      .getByRole('button', { name: 'Request rollback' })
      .evaluate((button) => {
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });

    const button = page
      .getByTestId('action-row-merge:work-9:3')
      .getByRole('button', { name: 'Requesting rollback' });
    await expect(button).toHaveAttribute('aria-busy', 'true');
    await expect(button).toBeDisabled();
    releaseRollback();
    await expect.poll(() => writes).toEqual([
      `/api/v1/tasks/${task.id}/actions/${mergeAction.action_id}/rollback`,
    ]);
  });

  test('shows the tier refusal when a member requests a rollback', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(
      (url) => url.pathname.endsWith('/rollback'),
      async (route) => {
        await route.fulfill({ status: 403, json: { detail: 'project admin required' } });
      },
    );

    await page.goto(`/tasks/${task.id}/actions`);
    await page
      .getByTestId('action-row-merge:work-9:3')
      .getByRole('button', { name: 'Request rollback' })
      .click();

    await expect(page.getByTestId('task-actions-rollback-error')).toHaveText(
      'project admin required',
    );
    await expect(page.getByTestId('action-row-merge:work-9:3')).toBeVisible();
  });

  test('keeps the rollback refusal when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let actionReads = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/actions`]: () => {
        actionReads += 1;
        return { actions: [mergeAction, deliverAction, failedMergeAction] };
      },
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'ACTION_RESULT_RECORDED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });
    await page.route(
      (url) => url.pathname.endsWith('/rollback'),
      async (route) => {
        await route.fulfill({ status: 403, json: { detail: 'project admin required' } });
      },
    );

    await page.goto(`/tasks/${task.id}/actions`);
    await expect(page.getByTestId('action-row-merge:work-9:3')).toBeVisible();
    const before = actionReads;
    await page
      .getByTestId('action-row-merge:work-9:3')
      .getByRole('button', { name: 'Request rollback' })
      .click();

    await expect(page.getByTestId('task-actions-rollback-error')).toHaveText(
      'project admin required',
    );
    releaseStream();
    await expect.poll(() => actionReads).toBeGreaterThan(before);
    await expect(page.getByTestId('task-actions-rollback-error')).toHaveText(
      'project admin required',
    );
    await expect(page.getByTestId('action-row-merge:work-9:3')).toBeVisible();
  });

  test('lists operator activity and follows its cursor', async ({ page }) => {
    const activityScopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/tasks/${task.id}/activity`) {
        activityScopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}/activity`);

    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toContainText(
      'apply_patch',
    );
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toContainText(
      'codex',
    );
    await page.getByRole('button', { name: 'Load more' }).click();
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-3')).toContainText(
      'Review found no blockers.',
    );
    await expect(page.getByRole('button', { name: 'Load more' })).toHaveCount(0);
    expect(activityScopes).toContain(project.id);
  });

  test('sends the source and Work filters to the activity route', async ({ page }) => {
    const queries: string[] = [];
    const activityScopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/activity`]: (url) => {
        queries.push(url.search);
        return activityFirstPage;
      },
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/tasks/${task.id}/activity`) {
        activityScopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}/activity`);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();
    expect(new Set(queries)).toEqual(new Set(['?limit=200']));
    await page.getByLabel('Source').selectOption('verifier');
    await page.getByLabel('Work id').fill('work-9');
    await page.getByRole('button', { name: 'Apply filters' }).click();

    await expect.poll(() => queries.some((query) => query.includes('source=verifier'))).toBe(true);
    await expect.poll(() => queries.some((query) => query.includes('work_id=work-9'))).toBe(true);
    expect(activityScopes).toContain(project.id);
  });

  test('pages with the applied filters, not the half-typed ones', async ({ page }) => {
    const queries: string[] = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/activity`]: (url) => {
        queries.push(url.search);
        return url.searchParams.get('cursor') === 'activity-page-2'
          ? activitySecondPage
          : activityFirstPage;
      },
    });

    await page.goto(`/tasks/${task.id}/activity`);
    await page.getByLabel('Source').selectOption('verifier');
    await page.getByRole('button', { name: 'Apply filters' }).click();
    await expect.poll(() => queries.some((query) => query.includes('source=verifier'))).toBe(true);

    await page.getByLabel('Work id').fill('work-9');
    await page.getByRole('button', { name: 'Load more' }).click();

    await expect
      .poll(() => queries.filter((query) => query.includes('cursor=')))
      .toEqual(['?source=verifier&cursor=activity-page-2&limit=200']);
  });

  test('drops an activity page that lands after the filters changed', async ({ page }) => {
    let releasePage2 = () => {};
    let page2Done = false;
    const page2Held = new Promise<void>((resolve) => {
      releasePage2 = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/activity`,
      async (route) => {
        const url = new URL(route.request().url());
        if (url.searchParams.get('cursor') === 'activity-page-2') {
          await page2Held;
          await route.fulfill({ json: activitySecondPage });
          page2Done = true;
          return;
        }
        if (url.searchParams.get('source') === 'verifier') {
          await route.fulfill({
            json: {
              items: [{ ...activityFirstPage.items[1], sequence: 7, summary: 'filtered line' }],
              next_cursor: null,
            },
          });
          return;
        }
        await route.fulfill({ json: activityFirstPage });
      },
    );

    await page.goto(`/tasks/${task.id}/activity`);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();
    await page.getByRole('button', { name: 'Load more' }).click();
    await page.getByLabel('Source').selectOption('verifier');
    await page.getByRole('button', { name: 'Apply filters' }).click();
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-7')).toBeVisible();
    releasePage2();

    await expect.poll(() => page2Done).toBe(true);
    await page.waitForTimeout(300);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-3')).toHaveCount(0);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toHaveCount(0);
  });

  test('says so when there is no operator activity yet', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/activity`]: () => ({ items: [], next_cursor: null }),
    });

    await page.goto(`/tasks/${task.id}/activity`);

    await expect(page.getByRole('heading', { name: 'No activity' })).toBeVisible();
  });

  test('offers the next activity page when the first page is empty', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/activity`,
      async (route) => {
        const url = new URL(route.request().url());
        await route.fulfill({
          json:
            url.searchParams.get('cursor') === 'activity-page-2'
              ? activitySecondPage
              : { items: [], next_cursor: 'activity-page-2' },
        });
      },
    );

    await page.goto(`/tasks/${task.id}/activity`);

    await expect(page.getByRole('heading', { name: 'No activity' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Load more' })).toBeVisible();
    await page.getByRole('button', { name: 'Load more' }).click();
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-3')).toBeVisible();
  });

  test('shows the next activity page in flight', async ({ page }) => {
    let releasePage2 = () => {};
    const page2Held = new Promise<void>((resolve) => {
      releasePage2 = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/activity`,
      async (route) => {
        const url = new URL(route.request().url());
        if (url.searchParams.get('cursor') === 'activity-page-2') {
          await page2Held;
          await route.fulfill({ json: activitySecondPage });
          return;
        }
        await route.fulfill({ json: activityFirstPage });
      },
    );

    await page.goto(`/tasks/${task.id}/activity`);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();
    await page.getByRole('button', { name: 'Load more' }).click();

    const button = page.getByRole('button', { name: 'Loading more activity' });
    await expect(button).toHaveAttribute('aria-busy', 'true');
    await expect(button).toBeDisabled();
    releasePage2();
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-3')).toBeVisible();
  });

  test('downloads the loaded activity', async ({ page }) => {
    let empty = true;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/activity`]: () =>
        empty ? { items: [], next_cursor: null } : activityFirstPage,
    });

    await page.goto(`/tasks/${task.id}/activity`);

    const button = page.getByRole('button', { name: 'Download loaded activity' });
    await expect(page.getByRole('heading', { name: 'No activity' })).toBeVisible();
    await expect(button).toBeDisabled();
    empty = false;
    await page.getByRole('button', { name: 'Apply filters' }).click();
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();

    const [download] = await Promise.all([page.waitForEvent('download'), button.click()]);
    expect(download.suggestedFilename()).toBe(`${task.id}-activity.json`);
    const path = await download.path();
    expect(path).not.toBeNull();
    const text = readFileSync(path as string, 'utf8');
    expect(text).toContain(activityFirstPage.items[0].summary);
  });

  test('shows the activity refusal the API stated', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });

    await page.goto(`/tasks/${task.id}`);
    await expect(page.getByRole('heading', { name: needsYouTask.title })).toBeVisible();

    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/activity`,
      async (route) => {
        await route.fulfill({
          status: 503,
          json: { detail: 'activity projection unavailable' },
        });
      },
    );
    await page.getByRole('link', { name: 'Activity' }).click();

    await expect(page.getByTestId('task-activity-error')).toHaveText(
      'activity projection unavailable',
    );
  });

  test('re-reads the activity when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let releaseActivityRead = () => {};
    let activityRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    const activityReadReady = new Promise<void>((resolve) => {
      releaseActivityRead = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/activity`,
      async (route) => {
        activityRequests += 1;
        if (activityRequests === 2) {
          await activityReadReady;
          await route.fulfill({ json: activitySecondPage });
          return;
        }
        await route.fulfill({ json: activityFirstPage });
      },
    );
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_STATUS_CHANGED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/activity`);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();
    const before = activityRequests;
    releaseStream();

    await expect.poll(() => activityRequests).toBeGreaterThan(before);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();
    releaseActivityRead();
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-3')).toBeVisible();
  });

  test('keeps the activity and states the refusal when a refetch fails', async ({ page }) => {
    let releaseStream = () => {};
    let activityRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/activity`]: () => {
        activityRequests += 1;
        return activityFirstPage;
      },
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_STATUS_CHANGED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/activity`);
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();
    const before = activityRequests;

    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/activity`,
      async (route) => {
        activityRequests += 1;
        await route.fulfill({
          status: 503,
          json: { detail: 'activity projection unavailable' },
        });
      },
    );
    releaseStream();

    await expect.poll(() => activityRequests).toBeGreaterThan(before);
    await expect(page.getByTestId('task-activity-error')).toHaveText(
      'activity projection unavailable',
    );
    await expect(page.getByTestId('activity-row-work-9-work-9:implement:1-1')).toBeVisible();
  });

  test('keeps the API order of the activity rows', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/activity`]: () => ({
        items: [...activityFirstPage.items].reverse(),
        next_cursor: null,
      }),
    });

    await page.goto(`/tasks/${task.id}/activity`);

    const rows = page.locator('[data-testid^="activity-row-"]');
    await expect(rows.first()).toContainText('just smoke');
    await expect(rows.last()).toContainText('apply_patch');
  });

  test('renders spend and stage attempts', async ({ page }) => {
    const telemetryRequests: Array<{ scope: string | undefined; search: string }> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'GET' && url.pathname === `/api/v1/tasks/${task.id}/telemetry`) {
        telemetryRequests.push({ scope: request.headers()['x-project-id'], search: url.search });
      }
    });

    await page.goto(`/tasks/${task.id}/telemetry`);

    const cycle = page.getByTestId('cycle-row-1');
    await expect(cycle).toContainText('Actual$0.42');
    await expect(cycle).toContainText('Reserved$0');
    await expect(cycle).toContainText('unknown');
    await expect(cycle).toContainText('0 free / 4 paid / 2 unpriced');
    await expect(cycle).toContainText('By device');
    await expect(cycle).toContainText('local x4');
    const attempts = page.getByTestId('work-telemetry-work-9');
    await expect(attempts).toContainText('implementer');
    await expect(attempts).not.toContainText('rung 0');
    await expect(attempts).toContainText('not priced');
    await expect(attempts).toContainText('$0.42');
    await expect(attempts).toContainText('escalated');
    await expect(attempts.locator('tbody tr').nth(3)).toContainText('repairer');
    await expect(attempts.locator('tbody tr').nth(3)).toContainText('running');
    await expect(attempts.locator('tbody tr').nth(3)).toContainText('unknown/unknown');
    await expect(attempts).toContainText('Verification: 1 of 1 runs passed.');
    await expect(page.getByTestId('work-telemetry-work-10')).toContainText(
      'No stage attempt has been recorded for this Work.',
    );
    await expect(page.getByTestId('schedule-health')).toHaveCount(0);
    await expect(page.getByTestId('project-escalation')).toContainText('implementer 25%');
    expect(telemetryRequests).toContainEqual({ scope: project.id, search: '' });
  });

  test('shows schedule health for a scheduled Task', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${scheduledTask.task_id}`]: () => scheduledTaskDetail,
      [`/api/v1/tasks/${scheduledTask.task_id}/telemetry`]: () => scheduledTelemetry,
      [`/api/v1/tasks/${scheduledTask.task_id}/events`]: () => '',
    });

    await page.goto(`/tasks/${scheduledTask.task_id}/telemetry`);

    await expect(page.getByTestId('schedule-health')).toContainText('100%');
    await expect(page.getByTestId('schedule-health')).toContainText('Consecutive failures');
    await expect(page.getByTestId('schedule-health')).toContainText('0');
    await expect(page.getByTestId('schedule-health')).toContainText('Overdue');
    await expect(page.getByTestId('schedule-health')).toContainText('no');
  });

  test('says so when no telemetry is available yet', async ({ page }) => {
    const emptyTelemetry = {
      ...telemetry,
      works: [],
      cycles: [],
      scheduled: null,
      project: { escalation_rate_per_role: {} },
    } satisfies TaskTelemetry;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/telemetry`]: () => emptyTelemetry,
    });

    await page.goto(`/tasks/${task.id}/telemetry`);

    await expect(page.getByRole('heading', { name: 'No telemetry yet' })).toBeVisible();
  });

  test('shows the telemetry refusal the API stated', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });

    await page.goto(`/tasks/${task.id}`);
    await expect(page.getByRole('heading', { name: needsYouTask.title })).toBeVisible();

    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/telemetry`,
      async (route) => {
        await route.fulfill({
          status: 503,
          json: { detail: 'telemetry projection unavailable' },
        });
      },
    );
    await page.getByRole('link', { name: 'Telemetry' }).click();

    await expect(page.getByTestId('task-telemetry-error')).toHaveText(
      'telemetry projection unavailable',
    );
  });

  test('does not show the empty state beside a refusal', async ({ page }) => {
    const emptyTelemetry = {
      ...telemetry,
      works: [],
      cycles: [],
      scheduled: null,
      project: { escalation_rate_per_role: {} },
    } satisfies TaskTelemetry;
    let releaseStream = () => {};
    let telemetryRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/telemetry`]: () => {
        telemetryRequests += 1;
        return emptyTelemetry;
      },
    });
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_STATUS_CHANGED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/telemetry`);
    await expect(page.getByRole('heading', { name: 'No telemetry yet' })).toBeVisible();
    const before = telemetryRequests;

    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/telemetry`,
      async (route) => {
        telemetryRequests += 1;
        await route.fulfill({
          status: 503,
          json: { detail: 'telemetry projection unavailable' },
        });
      },
    );
    releaseStream();

    await expect.poll(() => telemetryRequests).toBeGreaterThan(before);
    await expect(page.getByTestId('task-telemetry-error')).toHaveText(
      'telemetry projection unavailable',
    );
    await expect(page.getByRole('heading', { name: 'No telemetry yet' })).toHaveCount(0);
  });

  test('re-reads the telemetry when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let releaseTelemetryRead = () => {};
    let telemetryRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    const telemetryReadReady = new Promise<void>((resolve) => {
      releaseTelemetryRead = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/telemetry`,
      async (route) => {
        telemetryRequests += 1;
        if (telemetryRequests === 2) {
          await telemetryReadReady;
          await route.fulfill({
            json: {
              ...telemetry,
              cycles: [{ ...telemetry.cycles[0], usd_actual: '0.84' }],
            } satisfies TaskTelemetry,
          });
          return;
        }
        await route.fulfill({ json: telemetry });
      },
    );
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_STATUS_CHANGED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/telemetry`);
    await expect(page.getByTestId('cycle-row-1')).toContainText('$0.42');
    const before = telemetryRequests;
    releaseStream();

    await expect.poll(() => telemetryRequests).toBeGreaterThan(before);
    await expect(page.getByTestId('cycle-row-1')).toContainText('$0.42');
    releaseTelemetryRead();
    await expect(page.getByTestId('cycle-row-1')).toContainText('$0.84');
  });

  test('keeps the telemetry and states the refusal when a refetch fails', async ({ page }) => {
    let releaseStream = () => {};
    let failTelemetry = false;
    let telemetryRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail });
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/telemetry`,
      async (route) => {
        telemetryRequests += 1;
        if (failTelemetry) {
          await route.fulfill({
            status: 503,
            json: { detail: 'telemetry projection unavailable' },
          });
          return;
        }
        await route.fulfill({ json: telemetry });
      },
    );
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'TASK_STATUS_CHANGED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/telemetry`);
    await expect(page.getByTestId('cycle-row-1')).toBeVisible();
    const before = telemetryRequests;

    failTelemetry = true;
    releaseStream();

    await expect.poll(() => telemetryRequests).toBeGreaterThan(before);
    await expect(page.getByTestId('task-telemetry-error')).toHaveText(
      'telemetry projection unavailable',
    );
    await expect(page.getByTestId('cycle-row-1')).toBeVisible();
  });

  test('keeps the API order of the stage attempts', async ({ page }) => {
    const reversedTelemetry = {
      ...telemetry,
      works: [
        {
          ...telemetry.works[0],
          stage_attempts: [...telemetry.works[0].stage_attempts].reverse(),
        },
      ],
    } satisfies TaskTelemetry;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}`]: () => acceptedPlanDetail,
      [`/api/v1/tasks/${task.id}/telemetry`]: () => reversedTelemetry,
    });

    await page.goto(`/tasks/${task.id}/telemetry`);

    const rows = page.getByTestId('work-telemetry-work-9').locator('tbody tr');
    await expect(rows.first()).toContainText('repairer');
    await expect(rows.last()).toContainText('implementer');
  });

  test('shows the definition read-only and the triggers that can start a Task', async ({
    page,
  }) => {
    const requests: Array<{ path: string; scope: string | undefined; search: string }> = [];
    await selectProject(page);
    await mockCoordinatorApi(page);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (
        request.method() === 'GET' &&
        (url.pathname === `/api/v1/tasks/${task.id}` ||
          url.pathname === '/api/v1/tasks/triggers')
      ) {
        requests.push({
          path: url.pathname,
          scope: request.headers()['x-project-id'],
          search: url.search,
        });
      }
    });

    await page.goto(`/tasks/${task.id}/settings`);

    await expect(page.getByTestId('settings-target')).toContainText('sagewai/platform');
    await expect(page.getByTestId('settings-authority')).toContainText('plan: require');
    await expect(page.getByTestId('settings-routing')).toContainText(
      'analyst: harness:medium → claude:analysis',
    );
    await expect(page.getByTestId('settings-routing')).toContainText('implementer: codex');
    await expect(page.getByTestId('settings-schedule')).toContainText('runs once');
    await expect(page.getByTestId('settings-trigger-trigger-1')).toContainText('sagewai-task');
    await expect(page.getByLabel('Cycle limit (USD)')).toHaveValue(taskBudget.max_cycle_usd);
    await expect(page.getByLabel('Works per cycle')).toHaveValue(
      String(taskBudget.max_works_per_cycle),
    );
    await expect(page.getByLabel('Replans')).toHaveValue(String(taskBudget.max_replans));
    const used = needsYouTask.budget_used;
    await expect(page.getByTestId('settings-budget-used')).toContainText(
      `fenced on revision ${needsYouTask.revision}. Used so far: ` +
        `$${used.usd_actual} actual, $${used.usd_reserved} reserved, ` +
        `${used.usd_unknown} unpriced attempt(s).`,
    );
    expect(requests).toContainEqual({
      path: `/api/v1/tasks/${task.id}`,
      scope: project.id,
      search: '',
    });
    expect(requests).toContainEqual({
      path: '/api/v1/tasks/triggers',
      scope: project.id,
      search: '',
    });
  });

  test('says so when no trigger is configured', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      '/api/v1/tasks/triggers': () => ({ triggers: [] }),
    });

    await page.goto(`/tasks/${task.id}/settings`);

    await expect(page.getByRole('heading', { name: 'No triggers' })).toBeVisible();
    await expect(page.getByTestId('task-settings-error')).toHaveCount(0);
  });

  test('shows the settings refusal the API stated', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, {
      [`/api/v1/tasks/${task.id}/thread`]: () => ({
        task_id: task.id,
        project_id: task.project_id,
        brief_ref: null,
        entries: [],
        open_question_ids: [],
        pending_gate: null,
      }),
    });

    await page.goto(`/tasks/${task.id}`);
    await expect(page.getByRole('heading', { name: needsYouTask.title })).toBeVisible();

    await page.route(
      (url) => url.pathname === '/api/v1/tasks/triggers',
      async (route) => {
        await route.fulfill({ status: 503, json: { detail: 'settings projection unavailable' } });
      },
    );
    await page.getByRole('link', { name: 'Settings' }).click();

    await expect(page.getByTestId('task-settings-error')).toHaveText(
      'settings projection unavailable',
    );
  });

  test('re-reads the settings when the feed advances without blanking', async ({ page }) => {
    let releaseStream = () => {};
    let releaseTriggers = () => {};
    let holdTriggerRead = false;
    let triggerRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    const triggersReady = new Promise<void>((resolve) => {
      releaseTriggers = resolve;
    });
    const changedTrigger = {
      ...trigger,
      trigger_id: 'trigger-2',
      filter: { ...trigger.filter, label: 'needs-review' },
    } satisfies TaskTriggerSpec;
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === '/api/v1/tasks/triggers',
      async (route) => {
        triggerRequests += 1;
        if (holdTriggerRead) {
          await triggersReady;
          holdTriggerRead = false;
          await route.fulfill({ json: { triggers: [changedTrigger] } });
          return;
        }
        await route.fulfill({ json: { triggers: [trigger] } });
      },
    );
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'BUDGET_UPDATED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/settings`);
    await expect(page.getByTestId('settings-trigger-trigger-1')).toContainText('sagewai-task');
    const before = triggerRequests;
    holdTriggerRead = true;
    releaseStream();

    await expect.poll(() => triggerRequests).toBeGreaterThan(before);
    await expect(page.getByTestId('settings-trigger-trigger-1')).toContainText('sagewai-task');
    releaseTriggers();
    await expect(page.getByTestId('settings-trigger-trigger-2')).toContainText('needs-review');
  });

  test('keeps the settings and states the refusal when a refetch fails', async ({ page }) => {
    let releaseStream = () => {};
    let failTriggers = false;
    let triggerRequests = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === '/api/v1/tasks/triggers',
      async (route) => {
        triggerRequests += 1;
        if (failTriggers) {
          await route.fulfill({
            status: 503,
            json: { detail: 'settings projection unavailable' },
          });
          return;
        }
        await route.fulfill({ json: { triggers: [trigger] } });
      },
    );
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'BUDGET_UPDATED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/settings`);
    await expect(page.getByTestId('settings-trigger-trigger-1')).toContainText('sagewai-task');
    const before = triggerRequests;
    failTriggers = true;
    releaseStream();

    await expect.poll(() => triggerRequests).toBeGreaterThan(before);
    await expect(page.getByTestId('task-settings-error')).toHaveText(
      'settings projection unavailable',
    );
    await expect(page.getByTestId('settings-trigger-trigger-1')).toContainText('sagewai-task');
  });

  test('keeps the API order of the project triggers', async ({ page }) => {
    const secondTrigger = {
      ...trigger,
      trigger_id: 'trigger-2',
      filter: { ...trigger.filter, label: 'needs-review' },
    } satisfies TaskTriggerSpec;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      '/api/v1/tasks/triggers': () => ({ triggers: [secondTrigger, trigger] }),
    });

    await page.goto(`/tasks/${task.id}/settings`);

    const rows = page.locator('[data-testid^="settings-trigger-"]');
    await expect(rows.first()).toContainText('needs-review');
    await expect(rows.last()).toContainText('sagewai-task');
  });

  test('patches the budget at the Task revision', async ({ page }) => {
    const bodies: unknown[] = [];
    const scopes: Array<string | undefined> = [];
    let taskReads = 0;
    let patched = false;
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}`,
      async (route) => {
        if (route.request().method() === 'PATCH') {
          patched = true;
          await route.fulfill({
            json: {
              task: { ...task, budget: { ...taskBudget, max_cycle_usd: '25.00' } },
              record: { ...needsYouTask, revision: needsYouTask.revision + 1 },
            },
          });
          return;
        }
        taskReads += 1;
        await route.fulfill({
          json: patched
            ? {
                ...taskDetail,
                task: { ...task, budget: { ...taskBudget, max_cycle_usd: '25.00' } },
                record: { ...needsYouTask, revision: needsYouTask.revision + 1 },
              }
            : taskDetail,
        });
      },
    );
    page.on('request', (request) => {
      if (request.method() === 'PATCH' && request.url().includes('/api/v1/tasks/')) {
        bodies.push(JSON.parse(request.postData() ?? '{}'));
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto(`/tasks/${task.id}/settings`);
    await page.getByLabel('Cycle limit (USD)').fill('25.00');
    const before = taskReads;
    await page.getByRole('button', { name: 'Save budget' }).click();

    await expect(
      page.locator('[data-sonner-toast]').filter({ hasText: 'Budget updated' }),
    ).toBeVisible();
    await expect.poll(() => bodies).toEqual([
      { budget: { ...taskBudget, max_cycle_usd: '25.00' }, revision: needsYouTask.revision },
    ]);
    expect(scopes).toEqual([project.id]);
    await expect.poll(() => taskReads).toBeGreaterThan(before);
  });

  test('keeps the budget refusal when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let taskReads = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}`,
      async (route) => {
        if (route.request().method() === 'PATCH') {
          await route.fulfill({ status: 409, json: { detail: 'task revision moved' } });
          return;
        }
        taskReads += 1;
        await route.fulfill({ json: taskDetail });
      },
    );
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'BUDGET_UPDATED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/settings`);
    await page.getByLabel('Cycle limit (USD)').fill('25.00');
    await page.getByRole('button', { name: 'Save budget' }).click();

    await expect(page.getByTestId('task-settings-budget-error')).toHaveText(
      'task revision moved',
    );
    const before = taskReads;
    releaseStream();
    await expect.poll(() => taskReads).toBeGreaterThan(before);
    await expect(page.getByTestId('task-settings-budget-error')).toHaveText(
      'task revision moved',
    );
  });

  test('keeps the budget draft when the feed advances', async ({ page }) => {
    let releaseStream = () => {};
    let taskReads = 0;
    const streamReady = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}`,
      async (route) => {
        taskReads += 1;
        await route.fulfill({ json: taskDetail });
      },
    );
    await page.route(`**/api/v1/tasks/${task.id}/events`, async (route) => {
      await streamReady;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 5\nevent: task.updated\ndata: ${JSON.stringify({
          source: 'task_event',
          project_id: project.id,
          task_id: task.id,
          feed_sequence: 5,
          source_id: 'event-5',
          event_type: 'BUDGET_UPDATED',
          payload_json: {},
          created_at: '2026-09-01T11:35:00Z',
        })}\n\n`,
      });
    });

    await page.goto(`/tasks/${task.id}/settings`);
    await expect(page.getByLabel('Cycle limit (USD)')).toHaveValue(taskBudget.max_cycle_usd);
    await page.getByLabel('Cycle limit (USD)').fill('25.00');
    const before = taskReads;
    releaseStream();

    await expect.poll(() => taskReads).toBeGreaterThan(before);
    await expect(page.getByLabel('Cycle limit (USD)')).toHaveValue('25.00');
  });

  test('shows the budget patch in flight and sends it once', async ({ page }) => {
    const bodies: unknown[] = [];
    let releasePatch = () => {};
    let taskReads = 0;
    let patched = false;
    const patchReady = new Promise<void>((resolve) => {
      releasePatch = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}`,
      async (route) => {
        if (route.request().method() === 'PATCH') {
          bodies.push(JSON.parse(route.request().postData() ?? '{}'));
          await patchReady;
          patched = true;
          await route.fulfill({
            json: {
              task: { ...task, budget: { ...taskBudget, max_cycle_usd: '25.00' } },
              record: { ...needsYouTask, revision: needsYouTask.revision + 1 },
            },
          });
          return;
        }
        taskReads += 1;
        await route.fulfill({
          json: patched
            ? {
                ...taskDetail,
                task: { ...task, budget: { ...taskBudget, max_cycle_usd: '25.00' } },
                record: { ...needsYouTask, revision: needsYouTask.revision + 1 },
              }
            : taskDetail,
        });
      },
    );

    await page.goto(`/tasks/${task.id}/settings`);
    await page.getByLabel('Cycle limit (USD)').fill('25.00');
    const before = taskReads;
    await page.getByRole('button', { name: 'Save budget' }).evaluate((button) => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const button = page.getByRole('button', { name: 'Saving budget' });
    await expect(button).toHaveAttribute('aria-busy', 'true');
    await expect(button).toBeDisabled();
    releasePatch();
    await expect.poll(() => bodies).toEqual([
      { budget: { ...taskBudget, max_cycle_usd: '25.00' }, revision: needsYouTask.revision },
    ]);
    await expect.poll(() => taskReads).toBeGreaterThan(before);
  });

  test('answers once under a double click', async ({ page }) => {
    const writes: string[] = [];
    let releaseAnswer = () => {};
    const answerReady = new Promise<void>((resolve) => {
      releaseAnswer = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    recordWrites(page, writes);
    await page.route(
      (url) => url.pathname === `/api/v1/tasks/${task.id}/answers`,
      async (route) => {
        await answerReady;
        await route.fulfill({ json: needsYouTask });
      },
    );

    await page.goto(`/tasks/${task.id}`);
    await page.getByLabel('Answer to q-scope').fill('main');
    await page.getByRole('button', { name: 'Send answer' }).evaluate((button) => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    const button = page.getByRole('button', { name: 'Sending answer' });
    await expect(button).toHaveAttribute('aria-busy', 'true');
    await expect(button).toBeDisabled();
    releaseAnswer();
    await expect.poll(() => writes).toEqual([`/api/v1/tasks/${task.id}/answers`]);
  });

  test('decides a gate once under a double click', async ({ page }) => {
    const writes: string[] = [];
    let releaseGate = () => {};
    const gateReady = new Promise<void>((resolve) => {
      releaseGate = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page, { [`/api/v1/tasks/${task.id}/thread`]: () => thread });
    await mockTaskStream(page);
    recordWrites(page, writes);
    await page.route(
      (url) =>
        decodeURIComponent(url.pathname) === `/api/v1/tasks/${task.id}/gates/plan:${task.id}:1`,
      async (route) => {
        await gateReady;
        await route.fulfill({ json: { ...needsYouTask, pending_gate: null } });
      },
    );

    await page.goto(`/tasks/${task.id}`);
    await page
      .getByTestId(`gate-controls-plan:${task.id}:1`)
      .getByRole('button', { name: 'Allow' })
      .evaluate((button) => {
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });

    const button = page
      .getByTestId(`gate-controls-plan:${task.id}:1`)
      .getByRole('button', { name: 'Allowing' });
    await expect(button).toHaveAttribute('aria-busy', 'true');
    await expect(button).toBeDisabled();
    releaseGate();
    await expect.poll(() => writes).toEqual([`/api/v1/tasks/${task.id}/gates/plan:${task.id}:1`]);
  });
});
