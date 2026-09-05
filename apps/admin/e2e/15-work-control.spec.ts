import { test, expect, type Page } from '@playwright/test';

const project = {
  id: 'project-console',
  slug: 'console',
  name: 'Console Project',
  environment: 'production',
  allowed_origins: '',
  default_model: null,
  status: 'active',
  created_at: '2026-08-28T08:00:00Z',
  updated_at: '2026-08-28T08:00:00Z',
};

const isolatedProject = {
  ...project,
  id: 'project-isolated',
  slug: 'isolated',
  name: 'Isolated Project',
  environment: 'staging',
};

const work = {
  work_id: 'work-console-1',
  project_id: project.id,
  source_ref: 'https://github.com/sagewai/platform/issues/99',
  profile: 'software',
  status: 'READY_TO_DELIVER',
  contract_version: 1,
  active_run_id: 'run-codex-1',
  pending_gate: 'gate-production',
  profile_context: {},
  created_at: '2026-08-28T08:00:00Z',
  updated_at: '2026-08-28T09:00:00Z',
};

const pending = {
  attention_id: 'gate-production',
  project_id: project.id,
  work_id: work.work_id,
  kind: 'GATE_REQUESTED',
  source_ref: work.source_ref,
  summary: 'Approve production delivery?',
  severity: null,
  evidence_refs: ['evidence://review/accepted'],
  created_at: '2026-08-28T09:00:00Z',
};

const incidentPending = {
  ...pending,
  attention_id: 'external-outcome-incident',
  kind: 'EXTERNAL_OUTCOME_INCIDENT',
  summary: 'Configured outcome regressed after completion.',
  severity: 'high',
};

const events = [
  {
    id: 'event-1',
    project_id: project.id,
    work_id: work.work_id,
    sequence: 1,
    event_type: 'OPERATOR_DISCIPLINE_RECORDED',
    actor_type: 'operator',
    actor_ref: 'codex-worker-1',
    payload_json: {
      report: { verdict: 'pass', evidence_refs: ['evidence://discipline/pass'] },
    },
    created_at: '2026-08-28T08:05:00Z',
  },
  {
    id: 'event-2',
    project_id: project.id,
    work_id: work.work_id,
    sequence: 2,
    event_type: 'DEPLOYMENT_RECORDED',
    actor_type: 'delivery',
    actor_ref: 'cloudflare',
    payload_json: {
      action: 'deploy',
      deployment: {
        id: 'deployment-1',
        environment: 'production',
        exposure: 'canary',
        status: 'active',
        provider_ref: 'cloudflare://deployment-1',
        evidence_refs: ['evidence://deployment/canary'],
      },
    },
    created_at: '2026-08-28T08:10:00Z',
  },
  {
    id: 'event-3',
    project_id: project.id,
    work_id: work.work_id,
    sequence: 3,
    event_type: 'OBSERVATION_RECORDED',
    actor_type: 'delivery',
    actor_ref: 'observation_provider',
    payload_json: {
      observation: {
        deployment_id: 'deployment-1',
        verdict: 'fail',
        evidence_refs: ['evidence://observation/fail'],
      },
    },
    created_at: '2026-08-28T08:15:00Z',
  },
  {
    id: 'event-4',
    project_id: project.id,
    work_id: work.work_id,
    sequence: 4,
    event_type: 'ROLLBACK_RECORDED',
    actor_type: 'delivery',
    actor_ref: 'cloudflare',
    payload_json: {
      source_deployment_id: 'deployment-1',
      deployment: { status: 'rolled_back', provider_ref: 'cloudflare://rollback-1' },
      evidence_refs: ['evidence://rollback/safe'],
    },
    created_at: '2026-08-28T08:20:00Z',
  },
  {
    id: 'event-5',
    project_id: project.id,
    work_id: work.work_id,
    sequence: 5,
    event_type: 'TRIAGE_CREATED',
    actor_type: 'delivery',
    actor_ref: 'delivery_lifecycle',
    payload_json: {
      deployment_id: 'deployment-1',
      summary: 'Canary error rate exceeded the health gate.',
      evidence_refs: ['evidence://triage/report'],
    },
    created_at: '2026-08-28T08:25:00Z',
  },
];

async function mockWorkApi(
  page: Page,
  onScopedRequest?: () => void,
  failedProjectId?: string,
) {
  await page.route('**/api/v1/projects', async (route) => {
    await route.fulfill({ json: [project, isolatedProject] });
  });
  await page.route('**/api/v1/work**', async (route) => {
    const projectId = route.request().headers()['x-project-id'];
    if (projectId === project.id) {
      onScopedRequest?.();
    }
    if (projectId === failedProjectId) {
      await route.fulfill({ status: 503, json: { detail: 'project unavailable' } });
      return;
    }
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/v1/work/pending') {
      await route.fulfill({ json: [pending, incidentPending] });
      return;
    }
    if (pathname === `/api/v1/work/${work.work_id}`) {
      await route.fulfill({ json: { work, events } });
      return;
    }
    await route.fulfill({ json: [work] });
  });
}

test.describe('Work Control Console', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/v1/tasks/decisions', async (route) => {
      await route.fulfill({ json: { items: [] } });
    });
  });

  test('sends the explicit global Work scope', async ({ page }) => {
    const scopes: Array<string | undefined> = [];
    await mockWorkApi(page);
    await page.route('**/api/v1/work**', async (route) => {
      scopes.push(route.request().headers()['x-project-id']);
      await route.fulfill({ json: [] });
    });

    await page.goto('/work');
    await page.getByRole('button', { name: /Console Project/ }).click();
    await page.getByRole('button', { name: /All Projects/ }).click();

    await expect.poll(() => scopes.includes('global')).toBe(true);
  });


  test('waits for project hydration before replaying attention outside Work routes', async ({ page }) => {
    const pendingScopes: Array<string | undefined> = [];
    let releaseProjects!: () => void;
    const projectsReady = new Promise<void>((resolve) => {
      releaseProjects = resolve;
    });

    await page.addInitScript((slug) => {
      window.localStorage.setItem('sagewai-project', slug);
    }, project.slug);
    await page.route('**/api/v1/projects', async (route) => {
      await projectsReady;
      await route.fulfill({ json: [project, isolatedProject] });
    });
    await page.route('**/api/v1/work**', async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/api/v1/work/pending') {
        pendingScopes.push(route.request().headers()['x-project-id']);
        await route.fulfill({ json: [pending] });
        return;
      }
      await route.fulfill({ json: [work] });
    });

    await page.goto('/connections');
    await page.waitForTimeout(250);
    expect(pendingScopes).toEqual([]);

    releaseProjects();

    await expect.poll(() => pendingScopes.length).toBeGreaterThan(0);
    expect(pendingScopes).not.toContain('global');
    expect(pendingScopes.every((scope) => scope === project.id)).toBe(true);
    await expect(
      page.locator('[data-sonner-toast]').filter({
        hasText: 'Approval needed: Approve production delivery?',
      }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Coordinator, 1 items need attention' }),
    ).toBeVisible();
  });

  test('ignores an older same-scope attention response', async ({ page }) => {
    let holdNext = false;
    let newestAttention = false;
    let oldRequestStarted = false;
    let releaseOldRequest!: () => void;
    const oldRequestReady = new Promise<void>((resolve) => {
      releaseOldRequest = resolve;
    });

    await page.route('**/api/v1/projects', async (route) => {
      await route.fulfill({ json: [project] });
    });
    await page.route('**/api/v1/work**', async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname !== '/api/v1/work/pending') {
        await route.fulfill({ json: [work] });
        return;
      }
      if (holdNext) {
        holdNext = false;
        oldRequestStarted = true;
        await oldRequestReady;
        await route.fulfill({ json: [incidentPending] });
        return;
      }
      await route.fulfill({ json: newestAttention ? [pending] : [] });
    });

    await page.goto('/work');
    await expect(page.getByText('No pending attention')).toBeVisible();

    holdNext = true;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect.poll(() => oldRequestStarted).toBe(true);

    newestAttention = true;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(
      page.getByText('Approve production delivery?', { exact: true }).filter({ visible: true }),
    ).toBeVisible();

    releaseOldRequest();
    await page.waitForTimeout(250);

    await expect(
      page.getByText('Configured outcome regressed after completion.', { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-sonner-toast]').filter({
        hasText: 'External outcome incident',
      }),
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Coordinator, 1 items need attention' }),
    ).toBeVisible();
  });

  test('deduplicates a mirrored Work gate on the Coordinator badge', async ({ page }) => {
    const mirroredGateDecision = {
      kind: 'task',
      project_id: project.id,
      task_id: 'task-console-1',
      work_id: work.work_id,
      attention_id: 'gate-production',
      attention_version: null,
      summary: 'Approve production delivery?',
      urgency: 'now',
      due_at: '2026-08-28T09:00:00Z',
      gate_id: 'gate-production',
      decided_by: 'work',
      evidence_refs: ['evidence://review/accepted'],
    };
    // The mirrored gate counts once; a bare Work gate and a Task question count on their own.
    const bareWorkGate = { ...pending, attention_id: 'gate-staging', summary: 'Approve staging?' };
    const taskQuestion = {
      ...mirroredGateDecision,
      attention_id: 'question-1',
      gate_id: null,
      decided_by: null,
    };
    const bareGateItem = {
      ...mirroredGateDecision,
      kind: 'work',
      task_id: null,
      attention_id: 'gate-staging',
      gate_id: 'gate-staging',
    };

    await page.route('**/api/v1/projects', async (route) => {
      await route.fulfill({ json: [project] });
    });
    await page.route('**/api/v1/tasks/decisions', async (route) => {
      await route.fulfill({
        json: { items: [mirroredGateDecision, taskQuestion, bareGateItem] },
      });
    });
    await page.route('**/api/v1/work**', async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      await route.fulfill({
        json: pathname === '/api/v1/work/pending' ? [pending, bareWorkGate] : [work],
      });
    });

    await page.goto('/work');

    await expect(
      page.getByRole('button', { name: 'Coordinator, 3 items need attention' }),
    ).toBeVisible();
  });

  test('refreshes canonical attention on focus and avoids duplicate alerts', async ({ page }) => {
    let currentAttention: Array<typeof pending> = [];
    await page.route('**/api/v1/projects', async (route) => {
      await route.fulfill({ json: [project] });
    });
    await page.route('**/api/v1/work**', async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      await route.fulfill({
        json: pathname === '/api/v1/work/pending' ? currentAttention : [work],
      });
    });

    await page.goto('/work');
    await expect(page.getByText('No pending attention')).toBeVisible();

    currentAttention = [pending];
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));

    const alertToast = page.locator('[data-sonner-toast]').filter({
      hasText: 'Approval needed: Approve production delivery?',
    });
    await expect(alertToast).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Coordinator, 1 items need attention' }),
    ).toBeVisible();

    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await expect(alertToast).toHaveCount(1);

    await page.getByRole('button', { name: 'Collapse sidebar' }).click();
    await expect(
      page.getByRole('button', { name: 'Coordinator, 1 items need attention' }),
    ).toBeVisible();
  });

  test('shows project-scoped active Work and canonical pending attention', async ({ page }) => {
    let scopedRequests = 0;
    await mockWorkApi(
      page,
      () => { scopedRequests += 1; },
      isolatedProject.id,
    );

    await page.goto('/work');

    await expect(page.getByRole('heading', { name: 'Work Control' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Needs Attention' })).toBeVisible();
    await expect(page.getByText('Approve production delivery?', { exact: true }).filter({ visible: true })).toBeVisible();
    const incidentBadge = page.getByText('EXTERNAL OUTCOME INCIDENT', { exact: true }).filter({ visible: true });
    await expect(incidentBadge).toBeVisible();
    await expect(incidentBadge).toHaveClass(/text-destructive-foreground/);
    await expect(page.getByText('Configured outcome regressed after completion.', { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Coordinator, 2 items need attention' }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Active Work' })).toBeVisible();
    await expect(page.getByText('READY TO DELIVER').filter({ visible: true })).toBeVisible();
    await expect(page.getByRole('main').getByRole('link', { name: 'Fleet workers' })).toHaveAttribute('href', '/fleet');
    const activeWork = page.locator('[data-slot="card"]').filter({
      has: page.getByRole('heading', { name: 'Active Work' }),
    });
    await expect(activeWork.getByRole('link', { name: 'work-console-1' }).filter({ visible: true })).toHaveAttribute(
      'href',
      '/work/work-console-1',
    );
    await expect.poll(() => scopedRequests).toBeGreaterThan(0);

    await page.getByRole('button', { name: /Console Project/ }).click();
    await page.getByRole('button', { name: /Isolated Project/ }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Failed to load Work control state.' }),
    ).toHaveText('Failed to load Work control state.');
    await expect(page.getByText('work-console-1')).toHaveCount(0);
    await expect(page.getByText('Approve production delivery?', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Configured outcome regressed after completion.', { exact: true })).toHaveCount(0);
  });

  test('keeps the persisted project scope visible outside Work routes', async ({ page }) => {
    await mockWorkApi(page);

    await page.goto('/work');
    await page.getByRole('button', { name: /Console Project/ }).click();
    await page.getByRole('button', { name: /Isolated Project/ }).click();

    await page.goto('/connections');
    await expect(page.getByRole('button', { name: /Isolated Project/ })).toBeVisible();
  });

  test('shows canonical event actors, delivery history, and Evidence Board references', async ({ page }) => {
    await mockWorkApi(page);

    await page.goto(`/work/${work.work_id}`);

    await expect(page.getByRole('heading', { name: 'Work work-console-1' })).toBeVisible();
    await expect(page.getByText('operator · codex-worker-1')).toBeVisible();
    await expect(page.getByText('production · canary · active')).toBeVisible();
    await expect(page.getByText('FAIL', { exact: true })).toBeVisible();
    await expect(page.getByText('rolled back')).toBeVisible();
    await expect(page.getByText('Canary error rate exceeded the health gate.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Evidence Board' })).toBeVisible();
    const evidenceBoard = page.locator('[data-slot="card"]').filter({
      has: page.getByRole('heading', { name: 'Evidence Board' }),
    });
    await expect(evidenceBoard.getByText('evidence://observation/fail', { exact: true })).toBeVisible();
    await expect(evidenceBoard.getByText('evidence://rollback/safe', { exact: true })).toBeVisible();
  });

  test('puts Board, Tasks, Decisions and Active Work under Coordinator', async ({ page }) => {
    await mockWorkApi(page);

    await page.goto('/work');

    await expect(page.getByRole('link', { name: 'Board', exact: true })).toHaveAttribute('href', '/board');
    await expect(page.getByRole('link', { name: 'Tasks', exact: true })).toHaveAttribute('href', '/tasks');
    await expect(page.getByRole('link', { name: 'Decisions', exact: true })).toHaveAttribute('href', '/decisions');
    await expect(page.getByRole('link', { name: 'Active Work', exact: true })).toHaveAttribute('href', '/work');
    await expect(page.getByText('Autopilot (beta)')).toHaveCount(0);
  });
});
