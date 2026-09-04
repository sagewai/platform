'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Server } from 'lucide-react';

import { TaskCard } from '@/components/coordinator/task-card';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { BoardColumn, TaskRecord } from '@/utils/types';

const COLUMNS: { key: BoardColumn; label: string }[] = [
  { key: 'inbox', label: 'Inbox' },
  { key: 'needs_you', label: 'Needs you' },
  { key: 'planned', label: 'Planned' },
  { key: 'in_progress', label: 'In progress' },
  { key: 'done', label: 'Done' },
];

export default function CoordinatorBoardPage() {
  const { currentSlug, ready } = useProject();
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const needsProject = ready && currentSlug === null;

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setTasks([]);
    adminApi
      .getTaskBoard()
      .then((board) => {
        if (!cancelled) setTasks(COLUMNS.flatMap((column) => board.columns[column.key]));
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
  }, [ready, currentSlug]);

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
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          {COLUMNS.map((column) => {
            const cards = tasks.filter((task) => task.board_column === column.key);
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
    </div>
  );
}
