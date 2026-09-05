'use client';

import { use, useEffect, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useToast } from '@/components/ui/legacy';
import { Skeleton } from '@/components/ui/skeleton';
import { useTaskFeed } from '@/hooks/use-task-feed';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { Task, TaskDetail, TaskTriggerSpec } from '@/utils/types';

function targetLine(task: Task): string {
  if (task.target.kind === 'software') {
    return `${task.target.owner}/${task.target.repo} @ ${
      task.target.default_branch
    } · ${task.target.verification_commands.join(', ')}`;
  }
  return `report · ${task.target.sinks
    .map((sink) => `${sink.kind} v${sink.version}`)
    .join(', ')} · ${task.target.required_sections.length} required section(s)`;
}

function scheduleLine(task: Task): string {
  if (task.schedule === null) return 'runs once';
  return `${task.schedule.cron} (${task.schedule.timezone})${
    task.schedule.active ? '' : ' — paused'
  }`;
}

function authorityLine(task: Task): string {
  return [
    `plan: ${task.authority.plan}`,
    `merge: ${task.authority.merge}`,
    `replan: ${task.authority.replan}`,
    `deliver: ${task.authority.deliver}`,
  ].join(' · ');
}

function SettingsErrorLine({ testId, message }: { testId: string; message: string }) {
  return (
    <p role="alert" data-testid={testId} className="m-0 text-sm text-foreground">
      {message}
    </p>
  );
}

export default function TaskSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { currentSlug, ready } = useProject();
  const feed = useTaskFeed(id, ready && currentSlug !== null);
  const { toast } = useToast();
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [triggers, setTriggers] = useState<TaskTriggerSpec[]>([]);
  const [cycleUsd, setCycleUsd] = useState('');
  const [maxWorks, setMaxWorks] = useState('');
  const [maxReplans, setMaxReplans] = useState('');
  const [busy, setBusy] = useState(false);
  const [reloads, setReloads] = useState(0);
  const [error, setError] = useState('');
  const [budgetError, setBudgetError] = useState('');
  const identityRef = useRef('');
  const seedRef = useRef('');
  const busyRef = useRef(false);

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    const identity = `${currentSlug}:${id}`;
    if (identityRef.current !== identity) {
      identityRef.current = identity;
      setDetail(null);
      setTriggers([]);
      setCycleUsd('');
      setMaxWorks('');
      setMaxReplans('');
      setBudgetError('');
    }
    setError('');
    Promise.all([adminApi.getTask(id), adminApi.listTaskTriggers()])
      .then(([loaded, triggerPage]) => {
        if (cancelled) return;
        setDetail(loaded);
        setTriggers(triggerPage.triggers);
        if (seedRef.current !== identity) {
          seedRef.current = identity;
          setCycleUsd(loaded.task.budget.max_cycle_usd);
          setMaxWorks(String(loaded.task.budget.max_works_per_cycle));
          setMaxReplans(String(loaded.task.budget.max_replans));
        }
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, id, feed.revision, reloads]);

  async function saveBudget(snapshot: TaskDetail) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setBudgetError('');
    const { task, record } = snapshot;
    try {
      const updated = await adminApi.patchTaskBudget(
        id,
        {
          ...task.budget,
          max_cycle_usd: cycleUsd,
          max_works_per_cycle: Number(maxWorks),
          max_replans: Number(maxReplans),
        },
        record.revision,
      );
      setDetail((current) =>
        current === null ? current : { ...current, task: updated.task, record: updated.record },
      );
      setCycleUsd(updated.task.budget.max_cycle_usd);
      setMaxWorks(String(updated.task.budget.max_works_per_cycle));
      setMaxReplans(String(updated.task.budget.max_replans));
      toast('success', 'Budget updated');
      setReloads((count) => count + 1);
    } catch (cause) {
      setBudgetError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  if (detail === null) {
    return error === '' ? (
      <Skeleton aria-label="Loading Task settings" className="h-64 w-full" />
    ) : (
      <Card>
        <CardContent>
          <SettingsErrorLine testId="task-settings-error" message={error} />
        </CardContent>
      </Card>
    );
  }

  const { task, record } = detail;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="border-b">
          <CardTitle>
            <h2 className="m-0 text-base font-medium">Definition</h2>
          </CardTitle>
          <CardDescription className="text-foreground">
            What this Task was created with. Authority, routing, schedule and sinks are read-only
            here: each changes what the coordinator may do mid-cycle and needs its own event.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="font-medium">Target</dt>
            <dd className="m-0" data-testid="settings-target">
              {targetLine(task)}
            </dd>
            <dt className="font-medium">Execution</dt>
            <dd className="m-0">
              {task.execution.route}
              {task.execution.fleet_org_id === null ? '' : ` · ${task.execution.fleet_org_id}`}
            </dd>
            <dt className="font-medium">Schedule</dt>
            <dd className="m-0" data-testid="settings-schedule">
              {scheduleLine(task)}
            </dd>
            <dt className="font-medium">Authority</dt>
            <dd className="m-0" data-testid="settings-authority">
              {authorityLine(task)}
            </dd>
            <dt className="font-medium">Routing</dt>
            <dd className="m-0" data-testid="settings-routing">
              {Object.entries(task.routing.roles)
                .map(([role, ladder]) => `${role}: ${ladder.join(' → ')}`)
                .join(' · ') || 'template defaults'}
            </dd>
            <dt className="font-medium">Sensitivity</dt>
            <dd className="m-0">
              <Badge variant="outline">{task.sensitivity}</Badge>
            </dd>
            <dt className="font-medium">Retention</dt>
            <dd className="m-0">
              {task.retention_days === null ? 'project default' : `${task.retention_days} days`}
            </dd>
            <dt className="font-medium">Template</dt>
            <dd className="m-0">
              {task.template_id} v{task.template_version}
            </dd>
          </dl>
          {error !== '' && <SettingsErrorLine testId="task-settings-error" message={error} />}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b">
          <CardTitle>
            <h2 className="m-0 text-base font-medium">Budget</h2>
          </CardTitle>
          <CardDescription className="text-foreground" data-testid="settings-budget-used">
            Raising a budget is spending authority, so the change is admin-tier and is fenced on
            revision {record.revision}. Used so far:{' '}
            {'$' + record.budget_used.usd_actual} actual,{' '}
            {'$' + record.budget_used.usd_reserved} reserved, {record.budget_used.usd_unknown}{' '}
            unpriced attempt(s).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-3">
            <label className="flex flex-col gap-1 text-xs">
              Cycle limit (USD)
              <Input
                className="w-40"
                value={cycleUsd}
                onChange={(event) => setCycleUsd(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              Works per cycle
              <Input
                className="w-40"
                type="number"
                min={1}
                value={maxWorks}
                onChange={(event) => setMaxWorks(event.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              Replans
              <Input
                className="w-40"
                type="number"
                min={0}
                value={maxReplans}
                onChange={(event) => setMaxReplans(event.target.value)}
              />
            </label>
          </div>
          {budgetError !== '' && (
            <SettingsErrorLine testId="task-settings-budget-error" message={budgetError} />
          )}
          <Button
            aria-busy={busy}
            disabled={busy}
            variant="outline"
            onClick={() => void saveBudget(detail)}
          >
            {busy ? 'Saving budget' : 'Save budget'}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b">
          <CardTitle>
            <h2 className="m-0 text-base font-medium">Project triggers</h2>
          </CardTitle>
          <CardDescription className="text-foreground">
            Rules that turn an external event into a Task in this project. They are versioned and
            admin-approved; edit them with `sagewai task triggers` or the triggers routes.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {triggers.length === 0 ? (
            <div className="py-8 text-center">
              <h3 className="m-0 text-base font-semibold">No triggers</h3>
              <p className="m-0 mt-1 text-sm">No trigger is configured for this project.</p>
            </div>
          ) : (
            triggers.map((spec) => (
              <div
                key={spec.trigger_id}
                data-testid={`settings-trigger-${spec.trigger_id}`}
                className="rounded-lg border border-border p-3 text-sm"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-xs">{spec.trigger_id}</span>
                  <Badge variant={spec.enabled ? 'secondary' : 'outline'}>
                    {spec.enabled ? 'enabled' : 'disabled'}
                  </Badge>
                </div>
                <p className="m-0 mt-1">
                  {spec.source} ·{' '}
                  {Object.entries(spec.filter)
                    .map(([key, value]) => `${key}=${value}`)
                    .join(' ')}{' '}
                  → {spec.template_id} v{spec.template_version}
                </p>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
