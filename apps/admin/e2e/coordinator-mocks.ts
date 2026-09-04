import type { Page } from '@playwright/test';
import type {
  PendingAttention,
  Project,
  TaskBoard,
  TaskBudgetUsed,
  TaskDecisionItem,
  TaskPortfolio,
  TaskRecord,
} from '../utils/types';

export const project: Project = {
  id: 'project-console',
  slug: 'console',
  name: 'Console Project',
  environment: 'production',
  allowed_origins: '',
  default_model: null,
  status: 'active',
  created_at: '2026-09-01T08:00:00Z',
  updated_at: '2026-09-01T08:00:00Z',
};

const budgetUsed: TaskBudgetUsed = {
  works: 1,
  attempts: 2,
  replans: 0,
  seconds: 120,
  usd_actual: '1.50',
  usd_reserved: '0.50',
  usd_unknown: 0,
};

function record(overrides: Partial<TaskRecord> = {}): TaskRecord {
  return {
    task_id: 'task-1',
    project_id: project.id,
    kind: 'batch',
    origin: 'human',
    title: 'Ship the coordinator console',
    profile: 'software',
    status: 'PLANNING',
    last_event_sequence: 4,
    board_column: 'inbox',
    attention_owner: 'system',
    waiting_reason: 'working',
    current_cycle: 1,
    plan_version: 0,
    pending_gate: null,
    tracking_issue_url: null,
    pending_questions: 0,
    pending_material_questions: 0,
    next_run_at: null,
    lease_owner: null,
    lease_epoch: 0,
    lease_expires_at: null,
    revision: 3,
    budget_used: budgetUsed,
    created_at: '2026-09-01T09:00:00Z',
    updated_at: '2026-09-01T10:30:00Z',
    ...overrides,
  };
}

export const inboxTask = record({});

export const needsYouTask = record({
  task_id: 'task-2',
  title: 'Approve the weekly report delivery',
  status: 'PLAN_PROPOSED',
  board_column: 'needs_you',
  attention_owner: 'user',
  waiting_reason: 'gate:plan:task-2:1',
  pending_gate: 'plan:task-2:1',
  plan_version: 0,
  updated_at: '2026-09-01T11:30:00Z',
});

export const mirroredGateTask = record({
  task_id: 'task-3',
  title: 'Merge pull request 3',
  status: 'EXECUTING',
  board_column: 'needs_you',
  attention_owner: 'user',
  waiting_reason: 'gate:merge:work-9:3',
  pending_gate: 'merge:work-9:3',
  updated_at: '2026-09-01T12:00:00Z',
});

export const clarifyingTask = record({
  task_id: 'task-4',
  title: 'Clarify the target branch',
  status: 'CLARIFYING',
  board_column: 'needs_you',
  attention_owner: 'user',
  waiting_reason: 'questions:1',
  pending_questions: 1,
  pending_material_questions: 1,
  updated_at: '2026-09-01T12:30:00Z',
});

export const scheduledTask = record({
  task_id: 'task-5',
  title: 'Nightly dependency report',
  kind: 'scheduled',
  origin: 'schedule',
  status: 'SCHEDULED',
  board_column: 'planned',
  attention_owner: null,
  waiting_reason: null,
  profile: 'report',
  next_run_at: '2026-09-02T02:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
});

export const doneTask = record({
  task_id: 'task-6',
  title: 'Retire the Autopilot surface',
  status: 'COMPLETE',
  board_column: 'done',
  attention_owner: null,
  waiting_reason: null,
  updated_at: '2026-09-01T09:30:00Z',
});

const otherDoneTask = record({
  task_id: 'task-7',
  project_id: 'project-other',
  title: doneTask.title,
  status: 'COMPLETE',
  board_column: 'done',
  attention_owner: null,
  waiting_reason: null,
  updated_at: '2026-09-01T08:30:00Z',
});

export const portfolio: TaskPortfolio = {
  projects: [
    {
      project_id: project.id,
      tasks: [inboxTask, needsYouTask, mirroredGateTask, clarifyingTask, scheduledTask, doneTask],
      needs_you: 3,
    },
    { project_id: 'project-other', tasks: [otherDoneTask], needs_you: 0 },
  ],
};

/** Mirrors `GET /api/v1/tasks/board`: five columns, newest-touched first within each. */
export const board: TaskBoard = {
  columns: {
    inbox: [inboxTask],
    needs_you: [clarifyingTask, mirroredGateTask, needsYouTask],
    planned: [scheduledTask],
    in_progress: [],
    done: [doneTask],
  },
};

export const workDecision: TaskDecisionItem = {
  kind: 'task',
  project_id: project.id,
  task_id: mirroredGateTask.task_id,
  work_id: 'work-9',
  attention_id: 'merge:work-9:3',
  attention_version: null,
  summary: 'Merge pull request 3?',
  urgency: 'today',
  due_at: '2026-09-02T08:00:00Z',
  gate_id: 'merge:work-9:3',
  decided_by: 'work',
  evidence_refs: ['evidence://review/accepted', 'work-9'],
};

export const taskGateDecision: TaskDecisionItem = {
  kind: 'task',
  project_id: project.id,
  task_id: needsYouTask.task_id,
  work_id: null,
  attention_id: `plan:${needsYouTask.task_id}:1`,
  attention_version: null,
  summary: 'Approve the plan at version 1?',
  urgency: 'today',
  due_at: '2026-09-02T09:00:00Z',
  gate_id: `plan:${needsYouTask.task_id}:1`,
  decided_by: 'task',
  evidence_refs: [],
};

export const taskQuestionDecision: TaskDecisionItem = {
  kind: 'task',
  project_id: project.id,
  task_id: clarifyingTask.task_id,
  work_id: null,
  attention_id: 'q-scope',
  attention_version: 2,
  summary: 'Which branch should the change land on?',
  urgency: 'today',
  due_at: '2026-09-05T09:00:00Z',
  gate_id: null,
  decided_by: null,
  evidence_refs: [],
};

/** The Work half of the inbox as `GET /api/v1/work/pending` returns it. */
export const workPending: PendingAttention = {
  attention_id: workDecision.attention_id,
  project_id: project.id,
  work_id: 'work-9',
  kind: 'GATE_REQUESTED',
  source_ref: 'https://github.com/sagewai/platform/pull/3',
  summary: workDecision.summary,
  severity: null,
  evidence_refs: ['evidence://review/accepted'],
  created_at: '2026-09-01T08:00:00Z',
};

/** Route handlers keyed by decoded pathname; a spec passes extras or replaces one. */
export type Handlers = Record<string, (url: URL, method: string) => unknown>;

export const baseHandlers: Handlers = {
  '/api/v1/tasks/board': () => board,
  '/api/v1/tasks': (url) =>
    url.searchParams.get('cursor') === 'page-2'
      ? { tasks: [doneTask], next_cursor: null }
      : {
          tasks: [clarifyingTask, mirroredGateTask, needsYouTask, inboxTask, scheduledTask],
          next_cursor: 'page-2',
        },
  '/api/v1/tasks/decisions': () => ({
    items: [workDecision, taskGateDecision, taskQuestionDecision],
  }),
  '/api/v1/tasks/portfolio': () => portfolio,
};

export async function mockCoordinatorApi(page: Page, handlers: Handlers = {}): Promise<void> {
  const all = { ...baseHandlers, ...handlers };
  await page.route('**/api/v1/projects', async (route) => {
    await route.fulfill({ json: [project] });
  });
  await page.route(
    (url) => url.pathname === '/api/v1/work' || url.pathname.startsWith('/api/v1/work/'),
    async (route) => {
      const url = new URL(route.request().url());
      const handler = all[decodeURIComponent(url.pathname)];
      await route.fulfill(
        handler === undefined
          ? { json: [] }
          : { json: handler(url, route.request().method()) },
      );
    },
  );
  await page.route('**/api/v1/tasks**', async (route) => {
    const url = new URL(route.request().url());
    const pathname = decodeURIComponent(url.pathname);
    const handler = all[pathname];
    if (handler === undefined) {
      await route.fulfill({ status: 404, json: { detail: `unmocked ${pathname}` } });
      return;
    }
    await route.fulfill({ json: handler(url, route.request().method()) });
  });
}

export async function selectProject(page: Page): Promise<void> {
  await page.addInitScript((slug) => {
    window.localStorage.setItem('sagewai-project', slug);
  }, project.slug);
}

/** Every coordinator write this page makes, by decoded path, in the order it made them. */
export function recordWrites(page: Page, writes: string[]): void {
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() !== 'POST' && request.method() !== 'PATCH') return;
    if (!url.pathname.startsWith('/api/v1/tasks') && !url.pathname.startsWith('/api/v1/work')) return;
    writes.push(decodeURIComponent(url.pathname));
  });
}
