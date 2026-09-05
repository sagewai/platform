import { test, expect } from '@playwright/test';

import {
  mockCoordinatorApi,
  mockTaskStream,
  needsYouTask,
  project,
  recordWrites,
  selectProject,
  taskDetail,
  taskDetailTask as task,
  thread,
} from './coordinator-mocks';
import type { TaskDetail } from '../utils/types';

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
});
