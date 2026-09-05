'use client';

import { use, useEffect, useRef, useState } from 'react';

import { ResponsiveTable } from '@/components/responsive-table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useTaskFeed } from '@/hooks/use-task-feed';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { StageAttemptTelemetry, TaskTelemetry } from '@/utils/types';

const money = (value: string) => `$${value}`;

function TelemetryErrorLine({ message }: { message: string }) {
  return (
    <p role="alert" data-testid="task-telemetry-error" className="m-0 text-sm text-foreground">
      {message}
    </p>
  );
}

function tokens(attempt: StageAttemptTelemetry): string {
  const input = attempt.input_tokens === null ? 'unknown' : String(attempt.input_tokens);
  const output = attempt.output_tokens === null ? 'unknown' : String(attempt.output_tokens);
  return `${input}/${output}`;
}

function hasTelemetry(telemetry: TaskTelemetry): boolean {
  return (
    telemetry.cycles.length > 0 ||
    telemetry.works.length > 0 ||
    telemetry.scheduled !== null ||
    Object.keys(telemetry.project.escalation_rate_per_role).length > 0
  );
}

export default function TaskTelemetryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { currentSlug, ready } = useProject();
  const feed = useTaskFeed(id, ready && currentSlug !== null);
  const [telemetry, setTelemetry] = useState<TaskTelemetry | null>(null);
  const [error, setError] = useState('');
  const identityRef = useRef('');

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    const identity = `${currentSlug}:${id}`;
    if (identityRef.current !== identity) {
      identityRef.current = identity;
      setTelemetry(null);
    }
    setError('');
    adminApi
      .getTaskTelemetry(id)
      .then((loaded) => {
        if (!cancelled) setTelemetry(loaded);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, id, feed.revision]);

  const hasLoadedTelemetry = telemetry !== null && hasTelemetry(telemetry);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="border-b">
          <CardTitle>
            <h2 className="m-0 text-base font-medium">Spend per cycle</h2>
          </CardTitle>
          <CardDescription className="text-foreground">
            Settled and reserved dollars, the attempts nobody could price, and the worst case for
            the next attempt.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {telemetry === null && error === '' && (
            <Skeleton aria-label="Loading Task telemetry" className="h-64 w-full" />
          )}
          {telemetry !== null && !hasLoadedTelemetry && error === '' && (
            <div className="py-8 text-center">
              <h3 className="m-0 text-base font-semibold">No telemetry yet</h3>
              <p className="m-0 mt-1 text-sm">
                Runtime, spend and schedule health appear after the coordinator starts Work.
              </p>
            </div>
          )}
          {telemetry?.cycles.map((cycle) => (
            <div
              key={cycle.cycle}
              data-testid={`cycle-row-${cycle.cycle}`}
              className="rounded-lg border border-border p-3 text-sm"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">Cycle {cycle.cycle}</span>
                <Badge variant="secondary">{cycle.outcome ?? 'running'}</Badge>
              </div>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <dt>Actual</dt>
                <dd className="m-0">{money(cycle.usd_actual)}</dd>
                <dt>Reserved</dt>
                <dd className="m-0">{money(cycle.usd_reserved)}</dd>
                <dt>Limit</dt>
                <dd className="m-0">{money(cycle.limits.max_cycle_usd)}</dd>
                <dt>Worst case next attempt</dt>
                <dd className="m-0">
                  {cycle.worst_case_next_attempt === null
                    ? 'unknown - counted, not priced'
                    : money(cycle.worst_case_next_attempt)}
                </dd>
                <dt>Attempts</dt>
                <dd className="m-0">
                  {cycle.free_attempts} free / {cycle.paid_attempts} paid / {cycle.usd_unknown}{' '}
                  unpriced
                </dd>
                <dt>By device</dt>
                <dd className="m-0">
                  {Object.entries(cycle.by_device)
                    .map(([device, count]) => `${device} x${count}`)
                    .join(', ')}
                </dd>
              </dl>
            </div>
          ))}
          {error !== '' && <TelemetryErrorLine message={error} />}
        </CardContent>
      </Card>

      {telemetry?.works.map((work) => (
        <Card key={work.work_id} data-testid={`work-telemetry-${work.work_id}`}>
          <CardHeader className="border-b">
            <CardTitle>
              <h2 className="m-0 text-base font-medium">Stages of {work.work_id}</h2>
            </CardTitle>
            <CardDescription className="text-foreground">
              One row per stage attempt, in the order the ladder tried them.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {work.stage_attempts.length === 0 ? (
              <p className="m-0 text-sm">No stage attempt has been recorded for this Work.</p>
            ) : (
              <ResponsiveTable
                columns={[
                  { key: 'role', label: 'Role' },
                  { key: 'runtime', label: 'Runtime' },
                  { key: 'status', label: 'Status' },
                  { key: 'duration', label: 'Duration' },
                  { key: 'tokens', label: 'Tokens' },
                  { key: 'cost', label: 'Cost' },
                  { key: 'note', label: 'Note' },
                ]}
                rows={work.stage_attempts.map((attempt) => ({
                  role: attempt.role,
                  runtime: (
                    <span className="font-mono text-xs">
                      {attempt.runtime} (rung {attempt.position})
                    </span>
                  ),
                  status: <Badge variant="outline">{attempt.status ?? 'running'}</Badge>,
                  duration:
                    attempt.duration_seconds === null
                      ? '-'
                      : `${attempt.duration_seconds.toFixed(0)}s`,
                  tokens: tokens(attempt),
                  cost:
                    attempt.status === null
                      ? 'unknown'
                      : attempt.cost_usd !== null
                      ? `$${attempt.cost_usd.toFixed(2)}`
                      : 'not priced',
                  note:
                    attempt.escalation_reason === null
                      ? (attempt.review_verdict ?? '-')
                      : 'escalated',
                }))}
              />
            )}
            {work.verification_runs.length > 0 && (
              <p className="m-0 mt-3 text-xs">
                Verification: {work.verification_runs.filter((run) => run.passed).length} of{' '}
                {work.verification_runs.length} runs passed.
              </p>
            )}
          </CardContent>
        </Card>
      ))}

      {telemetry !== null && telemetry.scheduled !== null && (
        <Card data-testid="schedule-health">
          <CardHeader className="border-b">
            <CardTitle>
              <h2 className="m-0 text-base font-medium">Schedule health</h2>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt>Success rate</dt>
              <dd className="m-0">
                {telemetry.scheduled.success_rate === null
                  ? 'no completed cycle yet'
                  : `${Math.round(telemetry.scheduled.success_rate * 100)}%`}
              </dd>
              <dt>Consecutive failures</dt>
              <dd className="m-0">{telemetry.scheduled.consecutive_failures}</dd>
              <dt>Last success</dt>
              <dd className="m-0">
                {telemetry.scheduled.last_success_at === null
                  ? 'never'
                  : new Date(telemetry.scheduled.last_success_at).toLocaleString()}
              </dd>
              <dt>Overdue</dt>
              <dd className="m-0">{telemetry.scheduled.overdue ? 'yes' : 'no'}</dd>
            </dl>
          </CardContent>
        </Card>
      )}

      {hasLoadedTelemetry && (
        <Card data-testid="project-escalation">
          <CardHeader className="border-b">
            <CardTitle>
              <h2 className="m-0 text-base font-medium">Escalation rate in this project</h2>
            </CardTitle>
            <CardDescription className="text-foreground">
              How often each role left the first rung of its ladder, across every Work in the
              project.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="m-0 text-sm">
              {Object.entries(telemetry.project.escalation_rate_per_role)
                .map(([role, rate]) => `${role} ${Math.round(rate * 100)}%`)
                .join(' / ') || 'No runtime has been selected in this project yet.'}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
