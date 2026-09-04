'use client';

import Link from 'next/link';

import { Badge } from '@/components/ui/badge';
import type { TaskRecord, TaskStatus } from '@/utils/types';

const URGENT: ReadonlySet<TaskStatus> = new Set<TaskStatus>([
  'BLOCKED',
  'BUDGET_EXHAUSTED',
  'CONTROL_DEGRADED',
]);

export function displayLabel(value: string): string {
  return value.replaceAll('_', ' ').toLowerCase();
}

export function statusVariant(status: TaskStatus): 'destructive' | 'secondary' | 'outline' {
  if (URGENT.has(status)) return 'destructive';
  if (status === 'COMPLETE' || status === 'CANCELLED') return 'outline';
  return 'secondary';
}

export function formatMoment(value: string | null): string {
  return value === null ? '—' : new Date(value).toLocaleString();
}

export function TaskCard({ task }: { task: TaskRecord }) {
  return (
    <div
      data-testid={`task-card-${task.task_id}`}
      className="rounded-lg border border-border bg-background p-3"
    >
      <Link
        href={`/tasks/${encodeURIComponent(task.task_id)}`}
        className="font-medium text-primary no-underline hover:underline"
      >
        {task.title}
      </Link>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Badge variant="outline">{displayLabel(task.kind)}</Badge>
        <Badge variant="outline">{displayLabel(task.origin)}</Badge>
        <Badge variant={statusVariant(task.status)}>{displayLabel(task.status)}</Badge>
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <dt>Project</dt>
        <dd className="m-0 text-right">{task.project_id}</dd>
        <dt>Progress</dt>
        <dd className="m-0 text-right">cycle {task.current_cycle} · plan v{task.plan_version}</dd>
        <dt>Next run</dt>
        <dd className="m-0 text-right">{formatMoment(task.next_run_at)}</dd>
        <dt>Waiting on</dt>
        <dd className="m-0 text-right">
          {task.attention_owner ?? 'nobody'}
          {task.waiting_reason === null ? '' : ` · ${task.waiting_reason}`}
        </dd>
      </dl>
    </div>
  );
}
