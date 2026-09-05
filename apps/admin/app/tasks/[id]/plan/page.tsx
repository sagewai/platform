'use client';

import { use, useEffect, useRef, useState } from 'react';
import { ListChecks } from 'lucide-react';

import { ResponsiveTable } from '@/components/responsive-table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { useTaskFeed } from '@/hooks/use-task-feed';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { TaskDetail } from '@/utils/types';

function PlanErrorLine({ message }: { message: string }) {
  return (
    <p role="alert" data-testid="task-plan-error" className="m-0 text-sm text-foreground">
      {message}
    </p>
  );
}

function PlanError({ message }: { message: string }) {
  return (
    <Card>
      <CardContent>
        <PlanErrorLine message={message} />
      </CardContent>
    </Card>
  );
}

export default function TaskPlanPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { currentSlug, ready } = useProject();
  const feed = useTaskFeed(id, ready && currentSlug !== null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [error, setError] = useState('');
  const identityRef = useRef('');

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    const identity = `${currentSlug}:${id}`;
    if (identityRef.current !== identity) {
      identityRef.current = identity;
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
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, id, feed.revision]);

  if (detail === null) {
    return error === '' ? (
      <Skeleton aria-label="Loading Task plan" className="h-64 w-full" />
    ) : (
      <PlanError message={error} />
    );
  }
  if (detail.plan === null) {
    return error === '' ? (
      <EmptyState
        icon={ListChecks}
        title="No accepted plan"
        description="The coordinator proposes a plan once intake is settled; accepting it fills this tab."
      />
    ) : (
      <PlanError message={error} />
    );
  }

  const plan = detail.plan;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="border-b">
          <CardTitle>
            <h2 className="m-0 text-base font-medium">Plan version {plan.version}</h2>
          </CardTitle>
          <CardDescription className="text-foreground">
            The steps this cycle accepted, in dependency order, with what each one is allowed to
            touch and what it must satisfy.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {plan.steps.map((step) => (
            <div
              key={step.id}
              data-testid={`plan-step-${step.id}`}
              className="rounded-lg border border-border p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{step.title}</span>
                <span className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline">{step.domain}</Badge>
                  <Badge variant="outline">size {step.size}</Badge>
                  <Badge variant="secondary">risk {step.risk}</Badge>
                  {step.depends_on.length > 0 && (
                    <Badge variant="outline">after {step.depends_on.join(', ')}</Badge>
                  )}
                </span>
              </div>
              <p className="m-0 mt-1 text-sm">{step.goal}</p>
              <ul className="m-0 mt-2 list-disc pl-5 text-sm">
                {step.acceptance_criteria.map((criterion) => (
                  <li key={criterion.statement}>
                    {criterion.statement}{' '}
                    <span className="text-xs">({criterion.verification_kind})</span>
                  </li>
                ))}
              </ul>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <dt>Scope</dt>
                <dd className="m-0 break-all">{step.allowed_scope.join(', ')}</dd>
                {step.constraints.length > 0 && (
                  <>
                    <dt>Constraints</dt>
                    <dd className="m-0">{step.constraints.join('; ')}</dd>
                  </>
                )}
                {step.non_goals.length > 0 && (
                  <>
                    <dt>Not doing</dt>
                    <dd className="m-0">{step.non_goals.join('; ')}</dd>
                  </>
                )}
              </dl>
            </div>
          ))}
          {error !== '' && <PlanErrorLine message={error} />}
        </CardContent>
      </Card>

      <Card data-testid="acceptance-matrix">
        <CardHeader className="border-b">
          <CardTitle>
            <h2 className="m-0 text-base font-medium">Acceptance matrix</h2>
          </CardTitle>
          <CardDescription className="text-foreground">
            What the assessor checks at the merged head. A deterministic item names the command
            that decides it; an assessment item is read by the assessor.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ResponsiveTable
            columns={[
              { key: 'statement', label: 'Statement' },
              { key: 'kind', label: 'Verified by' },
              { key: 'command', label: 'Command' },
            ]}
            rows={plan.acceptance_matrix.map((item) => ({
              statement: item.statement,
              kind: <Badge variant="outline">{item.verification_kind}</Badge>,
              command:
                item.command === null ? (
                  '-'
                ) : (
                  <code className="font-mono text-xs">{item.command}</code>
                ),
            }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}
