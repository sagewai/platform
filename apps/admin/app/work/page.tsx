'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { AlertTriangle, Inbox, ListChecks, Server } from 'lucide-react';

import { ResponsiveTable } from '@/components/responsive-table';
import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import { useWorkAttention } from '@/utils/work-attention-context';
import type { PendingAttentionKind, WorkRecord } from '@/utils/types';

function displayLabel(value: string): string {
  return value.replaceAll('_', ' ');
}

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString();
}

function attentionClass(kind: PendingAttentionKind): string {
  if (kind === 'CONTROL_DEGRADED' || kind === 'EXTERNAL_OUTCOME_INCIDENT') {
    return 'border-destructive bg-destructive text-destructive-foreground';
  }
  if (kind === 'WORK_BLOCKED') {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300';
  }
  return 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300';
}

function statusClass(status: WorkRecord['status']): string {
  if (status === 'WORK_BLOCKED' || status === 'ROLLING_BACK' || status === 'TRIAGING') {
    return 'border-destructive bg-destructive text-destructive-foreground';
  }
  if (status === 'READY_TO_DELIVER' || status === 'SOAKING') {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300';
  }
  return 'border-border bg-muted text-muted-foreground';
}

export default function WorkControlPage() {
  const { currentSlug } = useProject();
  const {
    pending,
    loading: pendingLoading,
    error: pendingError,
  } = useWorkAttention();
  const [work, setWork] = useState<WorkRecord[]>([]);
  const [workLoading, setWorkLoading] = useState(true);
  const [workError, setWorkError] = useState('');
  const loading = workLoading || pendingLoading;
  const error = workError || pendingError || '';

  useEffect(() => {
    let cancelled = false;
    setWorkLoading(true);
    setWorkError('');
    setWork([]);

    adminApi.listActiveWork().then((activeWork) => {
      if (!cancelled) {
        setWork(activeWork);
      }
    }).catch(() => {
      if (!cancelled) {
        setWorkError('Failed to load Work control state.');
      }
    }).finally(() => {
      if (!cancelled) setWorkLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [currentSlug]);

  const pendingRows = pending.map((item) => ({
    kind: (
      <Badge variant="outline" className={attentionClass(item.kind)}>
        {displayLabel(item.kind)}
      </Badge>
    ),
    summary: (
      <div>
        <div className="font-medium text-foreground">{item.summary}</div>
        {item.severity && (
          <div className="mt-1 text-xs font-semibold uppercase text-destructive">
            {item.severity}
          </div>
        )}
      </div>
    ),
    work: (
      <Link
        href={`/work/${encodeURIComponent(item.work_id)}`}
        className="font-mono text-xs text-primary no-underline hover:underline"
      >
        {item.work_id}
      </Link>
    ),
    opened: <span className="text-muted-foreground">{formatTimestamp(item.created_at)}</span>,
  }));

  const workRows = work.map((item) => ({
    work: (
      <div>
        <Link
          href={`/work/${encodeURIComponent(item.work_id)}`}
          className="font-medium text-primary no-underline hover:underline"
        >
          {item.work_id}
        </Link>
        {item.source_ref && (
          <a
            href={item.source_ref}
            target="_blank"
            rel="noreferrer"
            className="mt-1 block max-w-sm truncate text-xs text-muted-foreground hover:text-foreground"
          >
            {item.source_ref}
          </a>
        )}
      </div>
    ),
    status: (
      <Badge variant="outline" className={statusClass(item.status)}>
        {displayLabel(item.status)}
      </Badge>
    ),
    profile: <span className="capitalize">{item.profile}</span>,
    updated: <span className="text-muted-foreground">{formatTimestamp(item.updated_at)}</span>,
  }));

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="m-0 font-[family-name:var(--font-heading)] text-2xl font-bold">
            Work Control
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Canonical lifecycle state and operator attention for {currentSlug ?? 'organization-global'} Work.
          </p>
        </div>
        <Link
          href="/fleet"
          className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-foreground no-underline hover:bg-muted"
        >
          <Server className="h-4 w-4" aria-hidden="true" />
          Fleet workers
        </Link>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      <Card>
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />
            <h2 className="m-0 text-base font-medium">Needs Attention</h2>
          </CardTitle>
          <CardDescription>
            The same approval gates, blocked questions, control degradations, and incidents shown by the CLI.
          </CardDescription>
          <CardAction>
            <Badge variant="secondary">{pending.length}</Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-3" aria-label="Loading pending attention">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : pending.length === 0 ? (
            <EmptyState
              icon={Inbox}
              title="No pending attention"
              description="There are no unresolved operator decisions for this project."
              className="border-0 py-8"
            />
          ) : (
            <ResponsiveTable
              columns={[
                { key: 'kind', label: 'Kind' },
                { key: 'summary', label: 'Question or condition' },
                { key: 'work', label: 'Work' },
                { key: 'opened', label: 'Opened' },
              ]}
              rows={pendingRows}
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="m-0 text-base font-medium">Active Work</h2>
          </CardTitle>
          <CardDescription>
            Current projections from the durable Work store. Completed Work is excluded.
          </CardDescription>
          <CardAction>
            <Badge variant="secondary">{work.length}</Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-3" aria-label="Loading active Work">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : work.length === 0 ? (
            <EmptyState
              icon={ListChecks}
              title="No active Work"
              description="Active WorkItems will appear here when their lifecycle begins."
              className="border-0 py-8"
            />
          ) : (
            <ResponsiveTable
              columns={[
                { key: 'work', label: 'Work' },
                { key: 'status', label: 'Status' },
                { key: 'profile', label: 'Profile' },
                { key: 'updated', label: 'Updated' },
              ]}
              rows={workRows}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
