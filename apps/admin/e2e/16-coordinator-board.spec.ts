import { test, expect } from '@playwright/test';

import {
  doneTask,
  inboxTask,
  mockCoordinatorApi,
  needsYouTask,
  scheduledTask,
  selectProject,
} from './coordinator-mocks';

test.describe('Coordinator board', () => {
  test('groups the project Tasks into the five columns', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto('/board');

    await expect(page.getByRole('heading', { name: 'Board' })).toBeVisible();
    await expect(page.getByTestId('board-column-needs_you')).toContainText(needsYouTask.title);
    await expect(page.getByTestId(`task-card-${needsYouTask.task_id}`)).toBeVisible();
    await expect(
      page
        .getByTestId(`task-card-${needsYouTask.task_id}`)
        .getByRole('link', { name: needsYouTask.title }),
    ).toHaveAttribute('href', `/tasks/${needsYouTask.task_id}`);
  });

  test('sends the selected project as the Task scope, never global', async ({ page }) => {
    const scopes: Array<string | undefined> = [];
    await selectProject(page);
    await mockCoordinatorApi(page);
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/v1/tasks')) {
        scopes.push(request.headers()['x-project-id']);
      }
    });

    await page.goto('/board');

    await expect.poll(() => scopes.length).toBeGreaterThan(0);
    expect(scopes.every((scope) => scope === 'project-console')).toBe(true);
  });

  test('reads the board route until a filter is set, then the list route', async ({ page }) => {
    const paths: string[] = [];
    await selectProject(page);
    await mockCoordinatorApi(page);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/v1/tasks' || url.pathname === '/api/v1/tasks/board') {
        paths.push(url.pathname + url.search);
      }
    });

    await page.goto('/board');
    await expect.poll(() => paths.some((path) => path.startsWith('/api/v1/tasks/board'))).toBe(true);

    await page.getByLabel('Kind').selectOption('scheduled');

    await expect.poll(() => paths.some((path) => path.includes('kind=scheduled'))).toBe(true);
    const listed = paths.filter((path) => path.startsWith('/api/v1/tasks?'));
    expect(
      listed.every((path) => path.includes('order_by=updated_at') && path.includes('descending=true')),
    ).toBe(true);
  });

  test('Focus hides what does not need you and All shows everything', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto('/board');
    await page.getByRole('button', { name: 'Focus', exact: true }).click();

    await expect(page.getByTestId(`task-card-${needsYouTask.task_id}`)).toBeVisible();
    await expect(page.getByTestId(`task-card-${inboxTask.task_id}`)).toHaveCount(0);

    await page.getByRole('button', { name: 'All', exact: true }).click();

    await expect(page.getByTestId(`task-card-${inboxTask.task_id}`)).toBeVisible();
    await expect(page.getByTestId(`task-card-${scheduledTask.task_id}`)).toBeVisible();
  });

  test('search filters the loaded cards by title', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto('/board');
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByLabel('Search Tasks').fill('dependency');

    await expect(page.getByTestId(`task-card-${scheduledTask.task_id}`)).toBeVisible();
    await expect(page.getByTestId(`task-card-${inboxTask.task_id}`)).toHaveCount(0);
  });

  test('Load more follows the cursor under the same ordering', async ({ page }) => {
    const cursored: string[] = [];
    await selectProject(page);
    await mockCoordinatorApi(page);
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/v1/tasks' && url.searchParams.get('cursor') !== null) {
        cursored.push(url.search);
      }
    });

    await page.goto('/board');
    await page.getByLabel('Kind').selectOption('batch');
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByRole('button', { name: 'Load more' }).click();

    await expect(page.getByTestId(`task-card-${doneTask.task_id}`)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Load more' })).toHaveCount(0);
    expect(
      cursored.every(
        (search) => search.includes('order_by=updated_at') && search.includes('descending=true'),
      ),
    ).toBe(true);
  });

  test('shows the API refusal when the board route fails', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === '/api/v1/tasks/board',
      async (route) => {
        await route.fulfill({ status: 503, json: { detail: 'project unavailable' } });
      },
    );

    await page.goto('/board');

    await expect(page.getByTestId('board-error')).toHaveText('project unavailable');
  });
});
