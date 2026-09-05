'use client';

import { use, useEffect, useRef, useState } from 'react';
import { ShieldCheck } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { useToast } from '@/components/ui/legacy';
import { Skeleton } from '@/components/ui/skeleton';
import { useTaskFeed } from '@/hooks/use-task-feed';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { TaskActionRecord } from '@/utils/types';

function outcome(action: TaskActionRecord): string {
  if (action.status === null) return 'in flight';
  if (action.passed === null) return `${action.status} - post-check unknown`;
  return `${action.status} - ${action.passed ? 'passed' : 'failed'}`;
}

function ActionsErrorLine({ testId, message }: { testId: string; message: string }) {
  return (
    <p role="alert" data-testid={testId} className="m-0 text-sm text-foreground">
      {message}
    </p>
  );
}

export default function TaskActionsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { currentSlug, ready } = useProject();
  const feed = useTaskFeed(id, ready && currentSlug !== null);
  const { toast } = useToast();
  const [actions, setActions] = useState<TaskActionRecord[] | null>(null);
  const [error, setError] = useState('');
  const [rollbackError, setRollbackError] = useState('');
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const identityRef = useRef('');
  const busyActionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    const identity = `${currentSlug}:${id}`;
    if (identityRef.current !== identity) {
      identityRef.current = identity;
      setActions(null);
      setRollbackError('');
    }
    setError('');
    adminApi
      .listTaskActions(id)
      .then((page) => {
        if (!cancelled) setActions(page.actions);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, id, feed.revision, reloads]);

  async function rollback(actionId: string) {
    if (busyActionRef.current !== null) return;
    busyActionRef.current = actionId;
    setBusyAction(actionId);
    setRollbackError('');
    try {
      await adminApi.requestTaskRollback(id, actionId);
      toast('success', `Rollback requested for ${actionId}`);
      setReloads((count) => count + 1);
    } catch (cause) {
      setRollbackError((cause as Error).message);
    } finally {
      busyActionRef.current = null;
      setBusyAction(null);
    }
  }

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle>
          <h2 className="m-0 text-base font-medium">Actions</h2>
        </CardTitle>
        <CardDescription className="text-foreground">
          Every side effect this Task performed, with the reversibility it was classified under,
          the rollback recipe recorded for it, and what its post-check saw. Requesting a rollback
          opens the gate; the coordinator runs the recipe.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {actions === null && error === '' && (
          <Skeleton aria-label="Loading Task actions" className="h-24 w-full" />
        )}
        {actions !== null && actions.length === 0 && error === '' && (
          <EmptyState
            icon={ShieldCheck}
            title="No actions yet"
            description="Nothing outside this project has been changed by this Task."
            className="border-0 py-8"
          />
        )}
        {actions !== null &&
          actions.map((action) => (
            <div
              key={action.action_id}
              data-testid={`action-row-${action.action_id}`}
              className="rounded-lg border border-border p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{action.action}</span>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline">{action.reversibility}</Badge>
                  <Badge variant="outline">risk {action.risk}</Badge>
                  <Badge variant="secondary">{outcome(action)}</Badge>
                </div>
              </div>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <dt>Work</dt>
                <dd className="m-0 font-mono">{action.work_id}</dd>
                <dt>Scope</dt>
                <dd className="m-0 break-all">{action.scope}</dd>
                <dt>Rollback recipe</dt>
                <dd className="m-0">{action.rollback ?? 'none - this action is irreversible'}</dd>
                <dt>Post-check</dt>
                <dd className="m-0">{action.post_check ?? 'none'}</dd>
                <dt>Requested</dt>
                <dd className="m-0">{new Date(action.requested_at).toLocaleString()}</dd>
              </dl>
              {action.detail !== null && <p className="m-0 mt-2 text-sm">{action.detail}</p>}
              {action.external_ref !== null && (
                <a
                  href={action.external_ref}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 block break-all text-xs text-primary hover:underline"
                >
                  {action.external_ref}
                </a>
              )}
              {action.evidence_refs.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {action.evidence_refs.map((reference) => (
                    <Badge key={reference} variant="outline" className="font-mono text-[10px]">
                      {reference}
                    </Badge>
                  ))}
                </div>
              )}
              {action.rollback !== null && action.status === 'succeeded' && (
                <Button
                  aria-busy={busyAction === action.action_id}
                  size="sm"
                  variant="outline"
                  className="mt-3"
                  disabled={busyAction !== null}
                  onClick={() => void rollback(action.action_id)}
                >
                  {busyAction === action.action_id ? 'Requesting rollback' : 'Request rollback'}
                </Button>
              )}
            </div>
          ))}
        {error !== '' && <ActionsErrorLine testId="task-actions-error" message={error} />}
        {rollbackError !== '' && (
          <ActionsErrorLine testId="task-actions-rollback-error" message={rollbackError} />
        )}
      </CardContent>
    </Card>
  );
}
