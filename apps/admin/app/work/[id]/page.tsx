'use client';

import Link from 'next/link';
import { use, useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ClipboardList,
  FileSearch,
  Server,
} from 'lucide-react';

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
import type { WorkDetail, WorkEvent, WorkRecord } from '@/utils/types';

interface Props {
  params: Promise<{ id: string }>;
}

function displayLabel(value: string): string {
  return value.replaceAll('_', ' ');
}

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString();
}

function objectValue(value: unknown): Record<string, unknown> | null {
  if (value === null || Array.isArray(value) || typeof value !== 'object') return null;
  return value as Record<string, unknown>;
}

function stringValue(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return null;
}

function exposureValue(value: unknown): string | null {
  const direct = stringValue(value);
  if (direct) return direct;
  const exposure = objectValue(value);
  if (!exposure) return null;
  const dimension = stringValue(exposure.dimension);
  const amount = stringValue(exposure.value);
  if (dimension && amount) return `${dimension} ${amount}`;
  return amount ?? dimension;
}

function collectEvidenceRefs(value: unknown): string[] {
  const refs = new Set<string>();

  function visit(item: unknown): void {
    if (Array.isArray(item)) {
      item.forEach(visit);
      return;
    }
    const record = objectValue(item);
    if (!record) return;
    for (const [key, nested] of Object.entries(record)) {
      if (key === 'evidence_refs' && Array.isArray(nested)) {
        for (const ref of nested) {
          if (typeof ref === 'string') refs.add(ref);
        }
        continue;
      }
      visit(nested);
    }
  }

  visit(value);
  return [...refs];
}

function statusClass(status: WorkRecord['status']): string {
  if (status === 'WORK_BLOCKED' || status === 'ROLLING_BACK' || status === 'TRIAGING') {
    return 'border-destructive/30 bg-destructive/10 text-destructive';
  }
  if (status === 'READY_TO_DELIVER' || status === 'SOAKING') {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300';
  }
  return 'border-border bg-muted text-muted-foreground';
}

function eventClass(event: WorkEvent): string {
  if (
    event.event_type === 'CONTROL_DEGRADED'
    || event.event_type === 'WORK_BLOCKED'
    || event.event_type === 'ROLLBACK_RECORDED'
    || event.event_type === 'TRIAGE_CREATED'
  ) {
    return 'border-l-4 border-l-destructive';
  }
  if (
    event.event_type === 'DEPLOYMENT_RECORDED'
    || event.event_type === 'OBSERVATION_RECORDED'
    || event.event_type === 'RELEASE_CREATED'
  ) {
    return 'border-l-4 border-l-blue-500';
  }
  return 'border-l-4 border-l-border';
}

function EventDetails({ event }: { event: WorkEvent }) {
  const payload = event.payload_json;

  if (event.event_type === 'DEPLOYMENT_RECORDED') {
    const deployment = objectValue(payload.deployment);
    if (!deployment) return null;
    const summary = [
      stringValue(deployment.environment),
      exposureValue(deployment.exposure),
      stringValue(deployment.status),
    ].filter(Boolean).join(' · ');
    return (
      <div className="space-y-1 text-sm">
        {summary && <div>{summary}</div>}
        {stringValue(payload.action) && (
          <div className="text-muted-foreground">Action: {stringValue(payload.action)}</div>
        )}
        {stringValue(deployment.provider_ref) && (
          <div className="break-all font-mono text-xs text-muted-foreground">
            {stringValue(deployment.provider_ref)}
          </div>
        )}
      </div>
    );
  }

  if (event.event_type === 'OBSERVATION_RECORDED') {
    const observation = objectValue(payload.observation);
    if (!observation) return null;
    const verdict = stringValue(observation.verdict);
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {verdict && (
          <Badge
            variant="outline"
            className={
              verdict === 'fail'
                ? 'border-destructive/30 bg-destructive/10 text-destructive'
                : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
            }
          >
            {verdict.toUpperCase()}
          </Badge>
        )}
        {stringValue(observation.deployment_id) && (
          <span className="text-muted-foreground">
            Deployment {stringValue(observation.deployment_id)}
          </span>
        )}
      </div>
    );
  }

  if (event.event_type === 'ROLLBACK_RECORDED') {
    const deployment = objectValue(payload.deployment);
    return (
      <div className="space-y-1 text-sm">
        <div className="font-medium">{displayLabel(stringValue(deployment?.status) ?? 'rolled_back')}</div>
        {stringValue(payload.source_deployment_id) && (
          <div className="text-muted-foreground">
            Source deployment: {stringValue(payload.source_deployment_id)}
          </div>
        )}
        {stringValue(deployment?.provider_ref) && (
          <div className="break-all font-mono text-xs text-muted-foreground">
            {stringValue(deployment?.provider_ref)}
          </div>
        )}
      </div>
    );
  }

  if (event.event_type === 'TRIAGE_CREATED') {
    return (
      <div className="space-y-1 text-sm">
        {stringValue(payload.summary) && <div>{stringValue(payload.summary)}</div>}
        {stringValue(payload.deployment_id) && (
          <div className="text-muted-foreground">
            Deployment {stringValue(payload.deployment_id)}
          </div>
        )}
      </div>
    );
  }

  if (event.event_type === 'RELEASE_CREATED') {
    const candidate = objectValue(payload.release_candidate);
    return (
      <div className="space-y-1 text-sm">
        {stringValue(candidate?.commit_sha) && (
          <div>Commit {stringValue(candidate?.commit_sha)}</div>
        )}
        {stringValue(candidate?.artifact_digest) && (
          <div className="break-all font-mono text-xs text-muted-foreground">
            {stringValue(candidate?.artifact_digest)}
          </div>
        )}
      </div>
    );
  }

  if (event.event_type === 'OPERATOR_DISCIPLINE_RECORDED') {
    const report = objectValue(payload.report) ?? payload;
    const verdict = stringValue(report.verdict);
    return verdict ? (
      <div className="text-sm">
        Discipline verdict: <span className="font-semibold uppercase">{verdict}</span>
      </div>
    ) : null;
  }

  const summary =
    stringValue(payload.question)
    ?? stringValue(payload.decision_request)
    ?? stringValue(payload.reason)
    ?? stringValue(payload.details)
    ?? stringValue(payload.summary)
    ?? stringValue(payload.stage)
    ?? stringValue(payload.action);

  return summary ? <div className="text-sm">{summary}</div> : null;
}

export default function WorkDetailPage({ params }: Props) {
  const { id } = use(params);
  const { currentSlug } = useProject();
  const [detail, setDetail] = useState<WorkDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');

    adminApi.getWork(id).then((result) => {
      if (!cancelled) setDetail(result);
    }).catch(() => {
      if (!cancelled) {
        setDetail(null);
        setError('This WorkItem was not found in the selected project.');
      }
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [currentSlug, id]);

  const evidenceRefs = useMemo(
    () => detail ? collectEvidenceRefs(detail.events.map((event) => event.payload_json)) : [],
    [detail],
  );

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Skeleton className="h-5 w-28" />
        <Skeleton className="h-9 w-72" />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        <Link href="/work" className="inline-flex items-center gap-1 text-sm text-primary no-underline hover:underline">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to Work
        </Link>
        <EmptyState
          icon={FileSearch}
          title="Work not found"
          description={error}
        />
      </div>
    );
  }

  const { work, events } = detail;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/work" className="inline-flex items-center gap-1 text-sm text-primary no-underline hover:underline">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back to Work
          </Link>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <h1 className="m-0 font-[family-name:var(--font-heading)] text-2xl font-bold">
              Work {work.work_id}
            </h1>
            <Badge variant="outline" className={statusClass(work.status)}>
              {displayLabel(work.status)}
            </Badge>
          </div>
          {work.source_ref && (
            <a
              href={work.source_ref}
              target="_blank"
              rel="noreferrer"
              className="mt-2 block break-all text-sm text-muted-foreground hover:text-foreground"
            >
              {work.source_ref}
            </a>
          )}
        </div>
        <Link
          href="/fleet"
          className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-foreground no-underline hover:bg-muted"
        >
          <Server className="h-4 w-4" aria-hidden="true" />
          Fleet workers
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card size="sm">
          <CardHeader>
            <CardDescription>Profile</CardDescription>
            <CardTitle className="capitalize">{work.profile}</CardTitle>
          </CardHeader>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardDescription>Active run</CardDescription>
            <CardTitle className="break-all font-mono text-sm">
              {work.active_run_id ?? 'None'}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardDescription>Pending gate</CardDescription>
            <CardTitle className="break-all font-mono text-sm">
              {work.pending_gate ?? 'None'}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardDescription>Updated</CardDescription>
            <CardTitle className="text-sm">{formatTimestamp(work.updated_at)}</CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2">
            <FileSearch className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="m-0 text-base font-medium">Evidence Board</h2>
          </CardTitle>
          <CardDescription>
            Canonical evidence references recorded by this Work lifecycle.
          </CardDescription>
          <CardAction>
            <Badge variant="secondary">{evidenceRefs.length}</Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          {evidenceRefs.length === 0 ? (
            <EmptyState
              icon={FileSearch}
              title="No evidence references"
              description="No event in this Work stream currently references Evidence Board material."
              className="border-0 py-8"
            />
          ) : (
            <ul className="m-0 space-y-2 p-0">
              {evidenceRefs.map((ref) => (
                <li
                  key={ref}
                  className="list-none break-all rounded-md border border-border bg-muted/40 px-3 py-2 font-mono text-xs"
                >
                  {ref}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2">
            <ClipboardList className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2 className="m-0 text-base font-medium">Timeline</h2>
          </CardTitle>
          <CardDescription>
            Immutable Work events in canonical sequence order, including operator and delivery activity.
          </CardDescription>
          <CardAction>
            <Badge variant="secondary">{events.length}</Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <EmptyState
              icon={ClipboardList}
              title="No events"
              description="No lifecycle events have been recorded for this WorkItem."
              className="border-0 py-8"
            />
          ) : (
            <ol className="m-0 space-y-3 p-0">
              {events.map((event) => {
                const refs = collectEvidenceRefs(event.payload_json);
                return (
                  <li key={event.id} className="list-none">
                    <Card size="sm" className={eventClass(event)}>
                      <CardHeader>
                        <CardTitle className="flex flex-wrap items-center gap-2">
                          <span>{displayLabel(event.event_type)}</span>
                          <span className="font-mono text-xs font-normal text-muted-foreground">
                            #{event.sequence}
                          </span>
                        </CardTitle>
                        <CardDescription>
                          {formatTimestamp(event.created_at)} · {event.actor_type}
                          {event.actor_ref ? ` · ${event.actor_ref}` : ''}
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        <EventDetails event={event} />
                        {refs.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {refs.map((ref) => (
                              <Badge key={ref} variant="outline" className="max-w-full font-mono text-[11px]">
                                <span className="truncate">{ref}</span>
                              </Badge>
                            ))}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  </li>
                );
              })}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
