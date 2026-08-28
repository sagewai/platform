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
      await route.fulfill({ json: [pending] });
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
    await expect(page.getByText('Approve production delivery?')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Active Work' })).toBeVisible();
    await expect(page.getByText('READY TO DELIVER')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Fleet workers' })).toHaveAttribute('href', '/fleet');
    await expect(page.getByRole('link', { name: 'work-console-1' }).first()).toHaveAttribute(
      'href',
      '/work/work-console-1',
    );
    await expect.poll(() => scopedRequests).toBeGreaterThan(0);

    await page.getByRole('button', { name: /Console Project/ }).click();
    await page.getByRole('button', { name: /Isolated Project/ }).click();
    await expect(page.getByRole('alert')).toContainText(
      'Failed to load Work control state.',
    );
    await expect(page.getByText('work-console-1')).toHaveCount(0);
    await expect(page.getByText('Approve production delivery?')).toHaveCount(0);
  });

  test('shows canonical event actors, delivery history, and Evidence Board references', async ({ page }) => {
    await mockWorkApi(page);

    await page.goto(`/work/${work.work_id}`);

    await expect(page.getByRole('heading', { name: 'Work work-console-1' })).toBeVisible();
    await expect(page.getByText('operator · codex-worker-1')).toBeVisible();
    await expect(page.getByText('production · canary · active')).toBeVisible();
    await expect(page.getByText('FAIL')).toBeVisible();
    await expect(page.getByText('rolled back')).toBeVisible();
    await expect(page.getByText('Canary error rate exceeded the health gate.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Evidence Board' })).toBeVisible();
    await expect(page.getByText('evidence://observation/fail')).toBeVisible();
    await expect(page.getByText('evidence://rollback/safe')).toBeVisible();
  });
});
