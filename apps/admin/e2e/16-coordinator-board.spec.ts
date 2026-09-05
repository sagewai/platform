import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import {
  board,
  clarifyingTask,
  doneTask,
  inboxTask,
  mockCoordinatorApi,
  needsYouTask,
  portfolio,
  scheduledTask,
  selectProject,
  task,
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

  test('shows Load more in flight', async ({ page }) => {
    let releasePage = () => {};
    const pageReady = new Promise<void>((resolve) => {
      releasePage = resolve;
    });
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === '/api/v1/tasks',
      async (route) => {
        const url = new URL(route.request().url());
        if (url.searchParams.get('cursor') === 'page-2') {
          await pageReady;
          await route.fulfill({ json: { tasks: [doneTask], next_cursor: null } });
          return;
        }
        await route.fulfill({
          json: {
            tasks: [clarifyingTask, needsYouTask, inboxTask],
            next_cursor: 'page-2',
          },
        });
      },
    );

    await page.goto('/board');
    await page.getByLabel('Kind').selectOption('batch');
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByRole('button', { name: 'Load more' }).click();

    const button = page.getByRole('button', { name: 'Loading more Tasks' });
    await expect(button).toHaveAttribute('aria-busy', 'true');
    await expect(button).toBeDisabled();
    releasePage();
    await expect(page.getByTestId(`task-card-${doneTask.task_id}`)).toBeVisible();
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

  test('previews the intake before it will create anything', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto('/board');
    await expect(page.getByRole('button', { name: 'Create Task' })).toBeDisabled();

    await page.getByLabel('Brief').fill('Ship the coordinator console');
    await page.getByRole('button', { name: 'Preview' }).click();

    const preview = page.getByTestId('intake-preview');
    await expect(preview).toContainText('software_delivery');
    await expect(preview).toContainText('batch');
    await expect(preview).toContainText('runs once');
    await expect(preview).toContainText('Which branch should the change land on?');
    await expect(preview).toContainText('sagewai/platform');
    await expect(preview).toContainText('opens one pull request');
    await expect(page.getByRole('button', { name: 'Create Task' })).toBeEnabled();
  });

  test('editing the brief withdraws the preview', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto('/board');
    await page.getByLabel('Brief').fill('Ship the coordinator console');
    await page.getByRole('button', { name: 'Preview' }).click();
    await expect(page.getByTestId('intake-preview')).toBeVisible();

    await page.getByLabel('Brief').fill('Ship something else');

    await expect(page.getByTestId('intake-preview')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Create Task' })).toBeDisabled();
  });

  test('creates the Task and reloads the board', async ({ page }) => {
    const createdRecord = { ...inboxTask, task_id: 'task-new', title: 'A new Task' };
    const postBodies: string[] = [];
    let created = false;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      '/api/v1/tasks/board': () => ({
        columns: {
          inbox: created ? [createdRecord] : [inboxTask],
          needs_you: [],
          planned: [],
          in_progress: [],
          done: [],
        },
      }),
      '/api/v1/tasks': (_url, method) => {
        if (method !== 'POST') return { tasks: [inboxTask], next_cursor: null };
        created = true;
        return { task: task(createdRecord), record: createdRecord };
      },
    });
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().endsWith('/api/v1/tasks')) {
        postBodies.push(request.postData() ?? '');
      }
    });

    await page.goto('/board');
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByLabel('Brief').fill('Ship the coordinator console');
    await page.getByRole('button', { name: 'Preview' }).click();
    await page.getByRole('button', { name: 'Create Task' }).click();

    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'task-new' })).toBeVisible();
    expect(postBodies.map((body) => JSON.parse(body))).toEqual([
      { brief: 'Ship the coordinator console' },
    ]);
    await expect(page.getByLabel('Brief')).toHaveValue('');
    await expect(page.getByTestId(`task-card-${createdRecord.task_id}`)).toBeVisible();
    await expect(page.getByTestId('board-column-inbox')).toContainText(createdRecord.title);
  });

  test('creates under the active kind filter and preserves the list query', async ({ page }) => {
    const createdRecord = { ...inboxTask, task_id: 'task-filtered', title: 'Filtered Task' };
    const listQueries: string[] = [];
    let boardReadsAfterCreate = 0;
    let created = false;
    await selectProject(page);
    await mockCoordinatorApi(page, {
      '/api/v1/tasks/board': () => {
        if (created) boardReadsAfterCreate += 1;
        return board;
      },
      '/api/v1/tasks': (url, method) => {
        if (method === 'POST') {
          created = true;
          return { task: task(createdRecord), record: createdRecord };
        }
        listQueries.push(url.search);
        return { tasks: created ? [createdRecord] : [inboxTask], next_cursor: null };
      },
    });

    await page.goto('/board');
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await page.getByLabel('Kind').selectOption('batch');
    await expect.poll(() => listQueries.some((query) => query.includes('kind=batch'))).toBe(true);
    const readsBeforeCreate = listQueries.length;
    await page.getByLabel('Brief').fill('Ship the coordinator console');
    await page.getByRole('button', { name: 'Preview' }).click();
    await page.getByRole('button', { name: 'Create Task' }).click();

    await expect(page.getByTestId(`task-card-${createdRecord.task_id}`)).toBeVisible();
    await expect(page.getByTestId('board-column-inbox')).toContainText(createdRecord.title);
    const reloadQueries = listQueries.slice(readsBeforeCreate);
    expect(reloadQueries.length).toBeGreaterThan(0);
    expect(
      reloadQueries.every(
        (query) =>
          query.includes('kind=batch') &&
          query.includes('order_by=updated_at') &&
          query.includes('descending=true'),
      ),
    ).toBe(true);
    expect(boardReadsAfterCreate).toBe(0);
  });

  test('fills the brief from a Markdown file', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);

    await page.goto('/board');
    await page.getByLabel('Markdown file').setInputFiles({
      name: 'brief.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# Coordinator\n\nShip the coordinator console'),
    });

    await expect(page.getByLabel('Brief')).toHaveValue(
      '# Coordinator\n\nShip the coordinator console',
    );
    await expect(page.getByRole('button', { name: 'Create Task' })).toBeDisabled();
  });

  test('shows the refusal when creation is rejected', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === '/api/v1/tasks',
      async (route) => {
        if (route.request().method() !== 'POST') {
          await route.fulfill({ json: { tasks: [], next_cursor: null } });
          return;
        }
        await route.fulfill({
          status: 409,
          json: { detail: 'target does not match the template' },
        });
      },
    );

    await page.goto('/board');
    await page.getByLabel('Brief').fill('Ship the coordinator console');
    await page.getByRole('button', { name: 'Preview' }).click();
    await page.getByRole('button', { name: 'Create Task' }).click();

    await expect(page.getByTestId('composer-error')).toHaveText(
      'target does not match the template',
    );
  });

  for (const theme of ['light', 'dark'] as const) {
    test(`a11y: ${theme} board error states — zero WCAG AA violations`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
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
      await page.route(
        (url) => url.pathname === '/api/v1/tasks/intake',
        async (route) => {
          await route.fulfill({ status: 503, json: { detail: 'intake unavailable' } });
        },
      );
      await page.getByLabel('Brief').fill('Ship the coordinator console');
      await page.getByRole('button', { name: 'Preview' }).click();
      await expect(page.getByTestId('composer-error')).toHaveText('intake unavailable');

      const results = await new AxeBuilder({ page })
        .include('main')
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      expect(
        results.violations,
        `${theme} board error violations:\n${JSON.stringify(results.violations, null, 2)}`,
      ).toEqual([]);
    });
  }
});

test.describe('Coordinator portfolio', () => {
  test('lists every project in the order returned by the portfolio route', async ({ page }) => {
    const portfolioRequests: Array<{ scope: string | undefined; search: string }> = [];
    await selectProject(page);
    await mockCoordinatorApi(page, {
      '/api/v1/tasks/portfolio': () => ({ projects: [...portfolio.projects].reverse() }),
    });
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/v1/tasks/portfolio') {
        portfolioRequests.push({ scope: request.headers()['x-project-id'], search: url.search });
      }
    });

    await page.goto('/tasks');

    await expect(page.getByRole('heading', { name: 'Tasks across your projects' })).toBeVisible();
    await expect.poll(() => portfolioRequests.length).toBeGreaterThan(0);
    expect(portfolioRequests[0]).toEqual({ scope: 'project-console', search: '?limit=20' });
    const cards = page.locator('[data-testid^="portfolio-project-"]');
    await expect(cards.first()).toContainText('project-other');
    await expect(page.getByTestId('portfolio-project-project-console')).toContainText(
      '3 need you',
    );
    await expect(page.getByTestId('portfolio-project-project-other')).toContainText(doneTask.title);
    await expect(
      page
        .getByTestId('portfolio-project-project-console')
        .getByRole('link', { name: inboxTask.title }),
    ).toHaveAttribute('href', `/tasks/${inboxTask.task_id}`);
  });

  test('shows the API refusal when the portfolio route fails', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page);
    await page.route(
      (url) => url.pathname === '/api/v1/tasks/portfolio',
      async (route) => {
        await route.fulfill({ status: 503, json: { detail: 'portfolio unavailable' } });
      },
    );

    await page.goto('/tasks');

    await expect(page.getByTestId('portfolio-error')).toHaveText('portfolio unavailable');
    await expect(page.getByRole('heading', { name: 'No Tasks yet' })).toHaveCount(0);
  });

  test('shows the empty state when no project has a Task', async ({ page }) => {
    await selectProject(page);
    await mockCoordinatorApi(page, { '/api/v1/tasks/portfolio': () => ({ projects: [] }) });

    await page.goto('/tasks');

    await expect(page.getByRole('heading', { name: 'No Tasks yet' })).toBeVisible();
  });
});
