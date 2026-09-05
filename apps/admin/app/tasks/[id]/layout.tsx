'use client';

import Link from 'next/link';
import { use, useEffect, useRef, useState, type ReactNode } from 'react';

import { displayLabel, statusVariant } from '@/components/coordinator/task-card';
import { TaskTabs } from '@/components/coordinator/task-tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { useToast } from '@/components/ui/legacy';
import { Skeleton } from '@/components/ui/skeleton';
import { useTaskFeed } from '@/hooks/use-task-feed';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { TaskDetail } from '@/utils/types';

export default function TaskLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { currentSlug, ready } = useProject();
  const { toast } = useToast();
  const feed = useTaskFeed(id, ready && currentSlug !== null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const identityRef = useRef('');
  const needsProject = ready && currentSlug === null;

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    const identity = `${currentSlug}:${id}`;
    const identityChanged = identityRef.current !== identity;
    const shouldShowLoading = identityChanged || detail === null;
    identityRef.current = identity;
    if (shouldShowLoading) {
      setLoading(true);
    }
    if (identityChanged) {
      setDetail(null);
    }
    setError('');
    adminApi
      .getTask(id)
      .then((loaded) => {
        if (!cancelled) setDetail(loaded);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      })
      .finally(() => {
        if (!cancelled && shouldShowLoading) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, id, feed.revision]);

  async function run(action: 'pause' | 'resume' | 'cancel') {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const record =
        action === 'pause'
          ? await adminApi.pauseTask(id)
          : action === 'resume'
            ? await adminApi.resumeTask(id)
            : await adminApi.cancelTask(id, null);
      setDetail((current) => (current === null ? current : { ...current, record }));
      const pastTense = {
        pause: 'paused',
        resume: 'resumed',
        cancel: 'cancelled',
      }[action];
      toast('success', `Task ${pastTense}: ${record.status}`);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  if (needsProject) {
    return (
      <div className="mx-auto max-w-5xl space-y-6" data-testid="task-page">
        <EmptyState
          title="Select a project"
          description="Task pages are scoped to one project."
        />
      </div>
    );
  }

  if (!loading && detail === null && error !== '') {
    return (
      <div className="mx-auto max-w-5xl space-y-6" data-testid="task-page">
        <Link
          href="/board"
          className="text-sm text-muted-foreground no-underline hover:text-foreground"
        >
          Back to board
        </Link>
        <EmptyState title="Task not found" description={error} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6" data-testid="task-page">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="m-0 font-[family-name:var(--font-heading)] text-2xl font-bold">
            {detail?.record.title ?? id}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            <span className="font-mono">{id}</span>
            {detail !== null &&
              ` · ${displayLabel(detail.record.kind)} · ${displayLabel(detail.record.origin)} · cycle ${detail.record.current_cycle} · plan v${detail.record.plan_version}`}
          </p>
          {detail?.record.tracking_issue_url != null && (
            <a
              href={detail.record.tracking_issue_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Tracking issue
            </a>
          )}
        </div>
        {loading ? (
          <div aria-label="Loading Task controls">
            <Skeleton className="h-8 w-64" />
          </div>
        ) : detail === null ? null : (
          <div className="flex flex-wrap items-center gap-2">
            <Badge data-testid="task-status" variant={statusVariant(detail.record.status)}>
              {displayLabel(detail.record.status).toUpperCase()}
            </Badge>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void run('pause')}>
              Pause
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void run('resume')}>
              Resume
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void run('cancel')}
            >
              Cancel
            </Button>
            <Link
              href="/board"
              className="text-sm text-muted-foreground no-underline hover:text-foreground"
            >
              Back to board
            </Link>
          </div>
        )}
      </div>

      {error !== '' && (
        <div
          role="alert"
          data-testid="task-error"
          className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {error}
        </div>
      )}
      {feed.error !== '' && <p className="m-0 text-sm text-muted-foreground">{feed.error}</p>}

      <TaskTabs taskId={id} />
      {children}
    </div>
  );
}
