import type { Page } from '@playwright/test';
import type {
  IntakePreview,
  PendingAttention,
  Project,
  Task,
  TaskBoard,
  TaskBudgetUsed,
  TaskDecisionItem,
  TaskDefaults,
  TaskDetail,
  TaskPortfolio,
  TaskRecord,
  TaskThread,
  TaskTemplateCatalogue,
  ThreadEntry,
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
  tracking_issue_url: 'https://github.com/sagewai/platform/issues/42',
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

export const templates: TaskTemplateCatalogue = {
  templates: [
    {
      id: 'software_delivery',
      version: '1',
      title: 'Software delivery',
      description: 'Plan, implement, verify and merge a bounded change.',
      category: 'software',
      kind: 'batch',
      profile: 'software',
    },
    {
      id: 'scheduled_research_report',
      version: '2',
      title: 'Scheduled research report',
      description: 'Compose, verify, review and deliver a recurring report.',
      category: 'report',
      kind: 'scheduled',
      profile: 'report',
    },
  ],
  reserved: ['event_triage', 'batch_extract'],
};

export const intakePreview: IntakePreview = {
  template_id: 'software_delivery',
  template_version: '1',
  band: 'auto_route',
  confidence: 0.92,
  candidates: ['software_delivery'],
  slots: { repository: 'sagewai/platform' },
  cron: null,
  timezone: 'UTC',
  questions: [
    {
      id: 'q-scope',
      text: 'Which branch should the change land on?',
      kind: 'text',
      options: [],
      default: 'main',
      defaultable: true,
      rationale: 'The default branch is assumed when nobody answers.',
      attention_version: 1,
    },
  ],
  preview: 'Reads the repository, opens one pull request, spends at most $10, asks before merging.',
};

export const softwareTarget = {
  kind: 'software',
  repository_path: '/srv/checkouts/platform',
  owner: 'sagewai',
  repo: 'platform',
  default_branch: 'main',
  verification_image: 'ghcr.io/sagewai/verify:1',
  verification_commands: ['just smoke'],
} satisfies Task['target'];

export const taskBudget = {
  max_works_per_cycle: 12,
  max_stage_attempts_per_cycle: 60,
  max_attempts_per_stage: 3,
  max_replans: 2,
  max_cycle_duration_seconds: 28800,
  max_cycle_usd: '10.00',
  claude_max_budget_usd_per_attempt: '5.00',
  harness_max_tokens_per_attempt: 200000,
  harness_max_tool_calls_per_attempt: 60,
  max_concurrent_works: 1,
} satisfies Task['budget'];

export const briefDigest = `sha256:${'a'.repeat(64)}`;

export const taskDefaults: TaskDefaults = {
  project_id: project.id,
  target: softwareTarget,
  execution: { route: 'local', fleet_org_id: null },
  timezone: 'UTC',
  clarification_deadline_seconds: 14400,
  routing: { roles: {}, prefer_free_implementation: false },
  harness_tiers: {},
  decision_channels: ['console'],
  revision: 0,
};

export function task(taskRecord: TaskRecord): Task {
  return {
    id: taskRecord.task_id,
    project_id: taskRecord.project_id,
    kind: taskRecord.kind,
    origin: taskRecord.origin,
    origin_ref: null,
    title: taskRecord.title,
    brief_ref: {
      project_id: taskRecord.project_id,
      digest: `sha256:${taskRecord.task_id}`,
      media_type: 'text/markdown',
      size_bytes: 128,
      storage_ref: `artifact://${taskRecord.task_id}/brief.md`,
      created_at: taskRecord.created_at,
      created_by: 'user',
    },
    brief_summary: taskRecord.title,
    source_ref: null,
    template_id:
      taskRecord.kind === 'scheduled' ? 'scheduled_research_report' : 'software_delivery',
    template_version: taskRecord.kind === 'scheduled' ? '2' : '1',
    slots: {},
    profile: taskRecord.profile,
    target: softwareTarget,
    schedule:
      taskRecord.kind === 'scheduled'
        ? { cron: '0 2 * * *', timezone: 'UTC', active: true }
        : null,
    budget: {
      max_works_per_cycle: 4,
      max_stage_attempts_per_cycle: 2,
      max_attempts_per_stage: 2,
      max_replans: 1,
      max_cycle_duration_seconds: 7200,
      max_cycle_usd: '10.00',
      claude_max_budget_usd_per_attempt: '5.00',
      harness_max_tokens_per_attempt: 20000,
      harness_max_tool_calls_per_attempt: 50,
      max_concurrent_works: 1,
    },
    authority: { plan: 'require', merge: 'require', replan: 'require', deliver: 'require' },
    routing: taskDefaults.routing,
    routing_version: 0,
    execution: taskDefaults.execution,
    sensitivity: 'internal',
    retention_days: null,
    tracking_issue_url: taskRecord.tracking_issue_url,
    created_by: 'user',
    created_at: taskRecord.created_at,
  };
}

export const composerHandlers: Handlers = {
  '/api/v1/tasks/templates': () => templates,
  '/api/v1/tasks/defaults': () => taskDefaults,
  '/api/v1/tasks/intake': () => intakePreview,
};

export const taskDetailTask = {
  ...task(needsYouTask),
  brief_ref: {
    project_id: project.id,
    digest: briefDigest,
    media_type: 'text/markdown',
    size_bytes: 42,
    storage_ref: `artifact://${briefDigest}`,
    created_at: '2026-09-01T09:00:00Z',
    created_by: 'admin',
  },
  slots: { repository: 'sagewai/platform' },
  budget: taskBudget,
  authority: {
    plan: 'require',
    merge: 'by_reversibility',
    replan: 'by_reversibility',
    deliver: 'by_reversibility',
  },
} satisfies Task;

export const taskDetail = { task: taskDetailTask, record: needsYouTask, plan: null } satisfies TaskDetail;

function threadEntry(overrides: Partial<ThreadEntry>): ThreadEntry {
  return {
    id: '1',
    sequence: 1,
    at: '2026-09-01T09:00:00Z',
    author: 'human',
    actor_ref: 'admin',
    kind: 'message',
    text: '',
    attention_id: null,
    attention_version: null,
    answer: null,
    answered_by: null,
    defaultable: null,
    deadline_at: null,
    gate_id: null,
    decided_by: null,
    work_id: null,
    decision: null,
    plan_version: null,
    refs: [],
    closed: false,
    ...overrides,
  };
}

export const thread = {
  task_id: taskDetailTask.id,
  project_id: project.id,
  brief_ref: `artifact://${briefDigest}`,
  entries: [
    threadEntry({
      id: '2',
      sequence: 2,
      kind: 'brief',
      text: 'Approve the weekly report delivery',
      refs: [`artifact://${briefDigest}`],
    }),
    threadEntry({
      id: '3',
      sequence: 3,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'message',
      text: 'Planning the change.',
    }),
    threadEntry({
      id: '4:q-scope',
      sequence: 4,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'question',
      text: 'Which branch should the change land on?',
      attention_id: 'q-scope',
      attention_version: 2,
      defaultable: true,
      deadline_at: '2026-09-01T13:00:00Z',
    }),
    threadEntry({
      id: '5',
      sequence: 5,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'gate',
      text: 'Approve the plan at version 1?',
      gate_id: 'plan:task-2:1',
      decided_by: 'task',
    }),
    threadEntry({
      id: '6',
      sequence: 6,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'status',
      text: 'PLAN_PROPOSED',
    }),
  ],
  open_question_ids: ['q-scope'],
  pending_gate: 'plan:task-2:1',
} satisfies TaskThread;

export const briefBody = '# Weekly report\n\nDeliver the weekly report to the console sink.\n';

/** One replay of the durable feed: the frames the Task page needs, in sequence order. */
export const feedFrames = [
  {
    id: 1,
    event: 'TASK_CREATED',
    data: {
      project_id: project.id,
      task_id: taskDetailTask.id,
      feed_sequence: 1,
      source: 'task_event',
      source_id: 'event-1',
      event_type: 'TASK_CREATED',
      payload_json: { title: taskDetailTask.title },
      created_at: '2026-09-01T09:00:00Z',
    },
  },
  {
    id: 4,
    event: 'CLARIFICATION_REQUESTED',
    data: {
      project_id: project.id,
      task_id: taskDetailTask.id,
      feed_sequence: 4,
      source: 'task_event',
      source_id: 'event-4',
      event_type: 'CLARIFICATION_REQUESTED',
      payload_json: {
        deadline_at: '2026-09-01T13:00:00Z',
        questions: [
          {
            id: 'q-scope',
            text: 'Which branch should the change land on?',
            kind: 'text',
            options: [],
            default: 'main',
            defaultable: true,
            rationale: 'The default branch is assumed when nobody answers.',
            attention_version: 2,
          },
        ],
      },
      created_at: '2026-09-01T09:30:00Z',
    },
  },
];

export function sseBody(frames = feedFrames): string {
  return frames
    .map(
      (frame) =>
        `id: ${frame.id}\nevent: ${frame.event}\ndata: ${JSON.stringify(frame.data)}\n\n`,
    )
    .join('');
}

/** The feed and the artifact are not JSON routes, so they get their own installer. */
export async function mockTaskStream(page: Page, body: string = sseBody()): Promise<void> {
  await page.route(`**/api/v1/tasks/${taskDetailTask.id}/events`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body });
  });
  await page.route('**/api/v1/artifacts/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/markdown', body: briefBody });
  });
}

export const taskHandlers: Handlers = {
  [`/api/v1/tasks/${taskDetailTask.id}`]: () => taskDetail,
  [`/api/v1/tasks/${taskDetailTask.id}/events`]: () => '',
};

export const answeredThread = {
  ...thread,
  entries: [
    threadEntry({
      id: '4:q-scope',
      sequence: 4,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'question',
      text: 'Which branch should the change land on?',
      attention_id: 'q-scope',
      attention_version: 2,
      answer: 'main',
      answered_by: 'default',
      defaultable: true,
      deadline_at: '2026-09-01T13:00:00Z',
    }),
  ],
  open_question_ids: [],
  pending_gate: null,
} satisfies TaskThread;

export const answerHandlers: Handlers = {
  [`/api/v1/tasks/${taskDetailTask.id}/answers`]: () => needsYouTask,
};

export const baseHandlers: Handlers = {
  ...composerHandlers,
  ...taskHandlers,
  ...answerHandlers,
  '/api/v1/tasks/board': () => board,
  '/api/v1/tasks': (url, method) => {
    if (method === 'POST') {
      const created = record({ task_id: 'task-new', title: 'A new Task' });
      return { task: task(created), record: created };
    }
    if (url.searchParams.get('cursor') === 'page-2') {
      return { tasks: [doneTask], next_cursor: null };
    }
    return {
      tasks: [clarifyingTask, mirroredGateTask, needsYouTask, inboxTask, scheduledTask],
      next_cursor: 'page-2',
    };
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
    if (pathname.endsWith('/events')) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: String(handler(url, route.request().method())),
      });
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
