import { test, expect } from '@playwright/test';

import {
  mockCoordinatorApi,
  needsYouTask,
  project,
  recordWrites,
  selectProject,
  taskDetail,
  taskDetailTask as task,
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
});
