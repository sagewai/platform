'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Server } from 'lucide-react';

import { TaskCard } from '@/components/coordinator/task-card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { BoardColumn, TaskKind, TaskRecord, TaskStatus } from '@/utils/types';

const COLUMNS: { key: BoardColumn; label: string }[] = [
  { key: 'inbox', label: 'Inbox' },
  { key: 'needs_you', label: 'Needs you' },
  { key: 'planned', label: 'Planned' },
  { key: 'in_progress', label: 'In progress' },
  { key: 'done', label: 'Done' },
];

const VIEWS = ['Focus', 'Today', 'All'] as const;
type View = (typeof VIEWS)[number];

const KINDS: TaskKind[] = ['batch', 'scheduled', 'event_driven'];

const STATUSES: TaskStatus[] = [
  'PLANNING',
  'CLARIFYING',
  'PLAN_PROPOSED',
  'EXECUTING',
  'ASSESSING',
  'SCHEDULED',
  'PAUSED',
  'BLOCKED',
  'BUDGET_EXHAUSTED',
  'CONTROL_DEGRADED',
  'COMPLETE',
  'CANCELLED',
];

const DAY_MS = 24 * 60 * 60 * 1000;

function dueAt(task: TaskRecord): number {
  return task.next_run_at === null ? Number.POSITIVE_INFINITY : Date.parse(task.next_run_at);
}

function inView(task: TaskRecord, view: View, now: number): boolean {
  if (view === 'All') return true;
  if (task.attention_owner === 'user') return true;
  return view === 'Today' && dueAt(task) <= now + DAY_MS;
}

export default function CoordinatorBoardPage() {
  const { currentSlug, ready } = useProject();
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [view, setView] = useState<View>('Focus');
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<TaskKind | ''>('');
  const [status, setStatus] = useState<TaskStatus | ''>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const needsProject = ready && currentSlug === null;
  const filterKey = `${currentSlug ?? ''}:${kind}:${status}`;
  const filterKeyRef = useRef(filterKey);
  filterKeyRef.current = filterKey;

  const load = useCallback(
    async (after: string | null) => {
      if (kind === '' && status === '') {
        const board = await adminApi.getTaskBoard();
        return { tasks: COLUMNS.flatMap((column) => board.columns[column.key]), cursor: null };
      }
      const page = await adminApi.listTasks({
        kind: kind === '' ? undefined : [kind],
        status: status === '' ? undefined : [status],
        order_by: 'updated_at',
        descending: true,
        limit: 50,
        cursor: after ?? undefined,
      });
      return { tasks: page.tasks, cursor: page.next_cursor };
    },
    [kind, status],
  );

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setTasks([]);
    setCursor(null);
    load(null)
      .then((next) => {
        if (!cancelled) {
          setTasks(next.tasks);
          setCursor(next.cursor);
        }
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, load]);

  const now = Date.now();
  const needle = search.trim().toLowerCase();
  const visible = tasks.filter(
    (task) =>
      inView(task, view, now) && (needle === '' || task.title.toLowerCase().includes(needle)),
  );

  async function loadMore() {
    const issuedFilterKey = filterKey;
    setError('');
    setLoadingMore(true);
    try {
      const next = await load(cursor);
      if (filterKeyRef.current !== issuedFilterKey) return;
      setTasks((current) => [...current, ...next.tasks]);
      setCursor(next.cursor);
    } catch (cause) {
      if (filterKeyRef.current === issuedFilterKey) setError((cause as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6" data-testid="coordinator-board-page">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="m-0 font-[family-name:var(--font-heading)] text-2xl font-bold">Board</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every Task in {currentSlug ?? 'the selected project'}, in the column its status and
            attention owner put it in.
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

      {!needsProject && error !== '' && (
        <div
          role="alert"
          data-testid="board-error"
          className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      {needsProject && (
        <EmptyState
          title="Select a project"
          description="The Task board is scoped to one project."
          className="py-8"
        />
      )}

      {!needsProject && (
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex gap-1" role="group" aria-label="Board view">
            {VIEWS.map((option) => (
              <Button
                key={option}
                size="sm"
                variant={view === option ? 'default' : 'outline'}
                aria-pressed={view === option}
                onClick={() => setView(option)}
              >
                {option}
              </Button>
            ))}
          </div>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Search Tasks
            <Input
              className="w-56"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Title contains..."
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Kind
            <select
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
              value={kind}
              onChange={(event) => setKind(event.target.value as TaskKind | '')}
            >
              <option value="">Any kind</option>
              {KINDS.map((option) => (
                <option key={option} value={option}>
                  {option.replaceAll('_', ' ')}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Status
            <select
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
              value={status}
              onChange={(event) => setStatus(event.target.value as TaskStatus | '')}
            >
              <option value="">Any status</option>
              {STATUSES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {!needsProject && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          {COLUMNS.map((column) => {
            const cards = visible.filter((task) => task.board_column === column.key);
            return (
              <Card key={column.key} data-testid={`board-column-${column.key}`}>
                <CardHeader className="border-b">
                  <CardTitle className="flex items-center justify-between gap-2">
                    <h2 className="m-0 text-sm font-medium">{column.label}</h2>
                    <Badge variant="secondary">{cards.length}</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {loading ? (
                    <div className="space-y-3" aria-label={`Loading ${column.label}`}>
                      <Skeleton className="h-24 w-full" />
                    </div>
                  ) : cards.length === 0 ? (
                    <EmptyState title="Nothing in this column." className="border-0 py-4" />
                  ) : (
                    <ul role="list" className="space-y-3">
                      {cards.map((task) => (
                        <li key={task.task_id}>
                          <TaskCard task={task} />
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {!needsProject && cursor !== null && (
        <Button variant="outline" onClick={() => void loadMore()} disabled={loadingMore}>
          Load more
        </Button>
      )}
    </div>
  );
}
