import type { Page } from '@playwright/test';
import type {
  IntakePreview,
  OperatorActivity,
  PendingAttention,
  Project,
  StageAttemptTelemetry,
  Task,
  TaskActionRecord,
  TaskActivityPage,
  TaskBoard,
  TaskBudgetUsed,
  TaskDecisionItem,
  TaskDefaults,
  TaskDetail,
  TaskPlan,
  TaskPortfolio,
  TaskRecord,
  TaskThread,
  TaskTemplateCatalogue,
  TaskTelemetry,
  TaskTriggerSpec,
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
  works: 2,
  attempts: 4,
  replans: 0,
  seconds: 120,
  usd_actual: '0.42',
  usd_reserved: '0',
  usd_unknown: 2,
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
    routing_version: 1,
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
  routing: {
    roles: {
      planner: ['claude:analysis'],
      designer: ['claude:analysis'],
      analyst: ['harness:medium', 'claude:analysis'],
      implementer: ['codex'],
      repairer: ['codex'],
      reviewer: ['claude:review'],
      assessor: ['claude:review'],
    },
    prefer_free_implementation: false,
  },
} satisfies Task;

export const taskPlan = {
  version: 1,
  steps: [
    {
      id: 'step-1',
      title: 'Add the coordinator board',
      goal: 'Render the five columns from the board route.',
      allowed_scope: ['apps/admin/app/board'],
      acceptance_criteria: [
        { statement: 'The board renders five columns.', verification_kind: 'deterministic' },
        { statement: 'The copy reads as one voice.', verification_kind: 'policy' },
      ],
      constraints: ['No new dependency.'],
      non_goals: ['Snooze and archive.'],
      risk: 'low',
      design_required: false,
      depends_on: [],
      domain: 'ui',
      size: 'm',
    },
    {
      id: 'step-2',
      title: 'Wire the decisions inbox',
      goal: 'Decide gates and answer questions in place.',
      allowed_scope: ['apps/admin/app/decisions'],
      acceptance_criteria: [
        {
          statement: 'A gate is decided on the route its decided_by names.',
          verification_kind: 'deterministic',
        },
      ],
      constraints: [],
      non_goals: [],
      risk: 'medium',
      design_required: false,
      depends_on: ['step-1'],
      domain: 'ui',
      size: 's',
    },
  ],
  acceptance_matrix: [
    {
      id: 'matrix-1',
      statement: 'The console suite passes.',
      verification_kind: 'deterministic',
      command: 'pnpm --filter @sagewai/admin exec playwright test 16-coordinator',
    },
    {
      id: 'matrix-2',
      statement: 'The board reads as a calm surface.',
      verification_kind: 'assessment',
      command: null,
    },
  ],
} satisfies TaskPlan;

export const taskDetail = { task: taskDetailTask, record: needsYouTask, plan: null } satisfies TaskDetail;

/** The same Task one gate later: version 1 accepted, so the detail route carries the plan. */
export const acceptedPlanDetail = {
  task: taskDetailTask,
  record: {
    ...needsYouTask,
    status: 'EXECUTING',
    board_column: 'in_progress',
    attention_owner: 'system',
    waiting_reason: 'working',
    pending_gate: null,
    plan_version: 1,
  },
  plan: taskPlan,
} satisfies TaskDetail;

export const scheduledTaskDetail = {
  task: task(scheduledTask),
  record: { ...scheduledTask, plan_version: 1 },
  plan: taskPlan,
} satisfies TaskDetail;

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

export const mirroredGateThread = {
  ...thread,
  task_id: mirroredGateTask.task_id,
  entries: [
    threadEntry({
      id: '7',
      sequence: 7,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'gate',
      text: 'Merge pull request 3?',
      gate_id: 'merge:work-9:3',
      decided_by: 'work',
      work_id: 'work-9',
    }),
  ],
  open_question_ids: [],
  pending_gate: mirroredGateTask.pending_gate,
} satisfies TaskThread;

/** The three gate states the thread must render without controls. */
export const settledGateThread = {
  ...thread,
  task_id: mirroredGateTask.task_id,
  entries: [
    threadEntry({
      id: '7',
      sequence: 7,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'gate',
      text: 'Deploy work-9 to production?',
      gate_id: 'deploy_production:work-9:1',
      decided_by: 'work',
      work_id: 'work-9',
    }),
    threadEntry({
      id: '8',
      sequence: 8,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'gate',
      text: 'Deliver the weekly report?',
      gate_id: `deliver:${mirroredGateTask.task_id}:1`,
      decided_by: 'task',
      decision: 'allow',
    }),
    threadEntry({
      id: '9',
      sequence: 9,
      author: 'system',
      actor_ref: 'coordinator',
      kind: 'gate',
      text: 'Approve the plan at version 1?',
      gate_id: `plan:${mirroredGateTask.task_id}:1`,
      decided_by: 'task',
      closed: true,
    }),
  ],
  open_question_ids: [],
  pending_gate: 'deploy_production:work-9:1',
} satisfies TaskThread;

export const gateHandlers: Handlers = {
  [`/api/v1/tasks/${taskDetailTask.id}/messages`]: () => needsYouTask,
  [`/api/v1/tasks/${taskDetailTask.id}/gates/plan:${taskDetailTask.id}:1`]: () => ({
    ...needsYouTask,
    pending_gate: null,
  }),
  '/api/v1/work/work-9/gates/merge:work-9:3': () => ({
    work_id: 'work-9',
    gate_id: 'merge:work-9:3',
    decision: 'allow',
  }),
};

export const mergeAction = {
  action_id: 'merge:work-9:3',
  work_id: 'work-9',
  action: 'merge_pull_request',
  reversibility: 'compensatable',
  risk: 'medium',
  scope: 'https://github.com/sagewai/platform/pull/3',
  rollback: 'revert_pull_request',
  post_check: 'merged_sha_read_back',
  gate_id: 'merge:work-9:3',
  requested_at: '2026-09-01T10:00:00Z',
  status: 'succeeded',
  external_ref: 'https://github.com/sagewai/platform/pull/3',
  completed_at: '2026-09-01T10:01:00Z',
  check: 'merged_sha_read_back',
  passed: true,
  detail: null,
  evidence_refs: ['evidence://merge/3'],
} satisfies TaskActionRecord;

export const deliverAction = {
  ...mergeAction,
  action_id: 'deliver:work-10:1',
  work_id: 'work-10',
  action: 'deliver_report',
  reversibility: 'irreversible',
  rollback: null,
  post_check: 'comment_read_back',
  gate_id: null,
  status: 'failed',
  external_ref: null,
  passed: false,
  detail: 'the sink refused the comment',
  evidence_refs: [],
} satisfies TaskActionRecord;

export const failedMergeAction = {
  ...mergeAction,
  action_id: 'merge:work-9:4',
  gate_id: 'merge:work-9:4',
  status: 'failed',
  external_ref: null,
  passed: false,
  detail: 'the merge was rejected',
} satisfies TaskActionRecord;

export const actionHandlers: Handlers = {
  [`/api/v1/tasks/${taskDetailTask.id}/actions`]: () => ({
    actions: [mergeAction, deliverAction, failedMergeAction],
  }),
  [`/api/v1/tasks/${taskDetailTask.id}/actions/${mergeAction.action_id}/rollback`]: () =>
    acceptedPlanDetail.record,
};

function activity(overrides: Partial<OperatorActivity> = {}): OperatorActivity {
  return {
    project_id: project.id,
    work_id: 'work-9',
    run_id: 'work-9:implement:1',
    sequence: 1,
    at: '2026-09-01T10:00:00Z',
    source: 'codex',
    kind: 'tool_call',
    summary: 'apply_patch packages/sdk/sagewai/work/tasks/views.py',
    detail: null,
    input_tokens: 1200,
    output_tokens: 340,
    cost_usd: null,
    ...overrides,
  };
}

export const activityFirstPage = {
  items: [
    activity({}),
    activity({ sequence: 2, source: 'verifier', kind: 'command', summary: 'just smoke' }),
  ],
  next_cursor: 'activity-page-2',
} satisfies TaskActivityPage;

export const activitySecondPage = {
  items: [
    activity({
      sequence: 3,
      source: 'claude',
      kind: 'message',
      summary: 'Review found no blockers.',
    }),
  ],
  next_cursor: null,
} satisfies TaskActivityPage;

export const activityHandlers: Handlers = {
  [`/api/v1/tasks/${taskDetailTask.id}/activity`]: (url) =>
    url.searchParams.get('cursor') === 'activity-page-2'
      ? activitySecondPage
      : activityFirstPage,
};

const stageAttempts: StageAttemptTelemetry[] = [
  {
    role: 'implementer',
    runtime: 'codex',
    position: 1,
    selection_note: null,
    started_at: '2026-09-01T10:00:00Z',
    duration_seconds: 92.5,
    status: 'failed',
    input_tokens: 1200,
    output_tokens: 340,
    cost_usd: null,
    cost_known: false,
    changed_files: 3,
    diff_lines: 84,
    verification_checks: [],
    review_verdict: null,
    finding_counts: {},
    escalation_reason: 'escalated',
  },
  {
    role: 'implementer',
    runtime: 'claude',
    position: 2,
    selection_note: null,
    started_at: '2026-09-01T10:05:00Z',
    duration_seconds: 41,
    status: 'succeeded',
    input_tokens: 900,
    output_tokens: 210,
    cost_usd: 0.42,
    cost_known: true,
    changed_files: null,
    diff_lines: null,
    verification_checks: [],
    review_verdict: null,
    finding_counts: {},
    escalation_reason: null,
  },
  {
    role: 'reviewer',
    runtime: 'codex',
    position: 1,
    selection_note: null,
    started_at: '2026-09-01T10:12:00Z',
    duration_seconds: 38,
    status: 'succeeded',
    input_tokens: 700,
    output_tokens: 160,
    cost_usd: null,
    cost_known: false,
    changed_files: null,
    diff_lines: null,
    verification_checks: [],
    review_verdict: 'repair',
    finding_counts: { major: 1 },
    escalation_reason: null,
  },
  {
    role: 'repairer',
    runtime: 'codex',
    position: 1,
    selection_note: null,
    started_at: '2026-09-01T10:18:00Z',
    duration_seconds: null,
    status: null,
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    cost_known: false,
    changed_files: null,
    diff_lines: null,
    verification_checks: [],
    review_verdict: null,
    finding_counts: {},
    escalation_reason: null,
  },
];

const scheduledHealth = {
  cycles: [
    {
      cycle: 1,
      status: 'succeeded',
      completed_at: '2026-09-01T10:20:00Z',
      duration_seconds: 1200,
      usd_actual: '0.42',
    },
  ],
  success_rate: 1,
  consecutive_failures: 0,
  last_success_at: '2026-09-01T10:20:00Z',
  overdue: false,
} satisfies NonNullable<TaskTelemetry['scheduled']>;

export const telemetry = {
  task_id: taskDetailTask.id,
  project_id: project.id,
  works: [
    {
      work_id: 'work-9',
      stage_attempts: stageAttempts,
      verification_runs: [
        { attempt_id: 'work-9:verify:1', at: '2026-09-01T10:10:00Z', passed: true, checks: [] },
      ],
      stage_timeline: [{ stage: 'implement', status: 'completed', at: '2026-09-01T10:02:00Z' }],
      attention_history: [],
    },
    {
      work_id: 'work-10',
      stage_attempts: [],
      verification_runs: [],
      stage_timeline: [],
      attention_history: [],
    },
  ],
  cycles: [
    {
      cycle: 1,
      outcome: 'succeeded',
      usd_actual: '0.42',
      usd_reserved: '0',
      usd_unknown: 2,
      limits: taskBudget,
      worst_case_next_attempt: null,
      free_attempts: 0,
      paid_attempts: 4,
      by_device: { local: 4 },
      burn_series: [{ at: '2026-09-01T10:05:00Z', usd_actual: '0.42' }],
    },
  ],
  scheduled: null,
  project: { escalation_rate_per_role: { implementer: 0.25 } },
} satisfies TaskTelemetry;

export const scheduledTelemetry = {
  ...telemetry,
  task_id: scheduledTask.task_id,
  cycles: [{ ...telemetry.cycles[0], limits: scheduledTaskDetail.task.budget }],
  scheduled: scheduledHealth,
} satisfies TaskTelemetry;

export const telemetryHandlers: Handlers = {
  [`/api/v1/tasks/${taskDetailTask.id}/telemetry`]: () => telemetry,
};

export const trigger = {
  trigger_id: 'trigger-1',
  project_id: project.id,
  source: 'github_label',
  filter: { owner: 'sagewai', repo: 'platform', label: 'sagewai-task' },
  template_id: 'software_delivery',
  template_version: '1',
  slots: {},
  authority: { plan: 'require', merge: 'require', replan: 'require', deliver: 'require' },
  enabled: true,
} satisfies TaskTriggerSpec;

export const settingsHandlers: Handlers = {
  '/api/v1/tasks/triggers': () => ({ triggers: [trigger] }),
};

export const baseHandlers: Handlers = {
  ...composerHandlers,
  ...taskHandlers,
  ...answerHandlers,
  ...gateHandlers,
  ...actionHandlers,
  ...activityHandlers,
  ...telemetryHandlers,
  ...settingsHandlers,
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
