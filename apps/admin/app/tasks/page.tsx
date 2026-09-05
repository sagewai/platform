'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Inbox } from 'lucide-react';

import { displayLabel, formatMoment, statusVariant } from '@/components/coordinator/task-card';
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
import type { TaskPortfolioEntry } from '@/utils/types';

export default function TaskPortfolioPage() {
  const { currentSlug, ready } = useProject();
  const [projects, setProjects] = useState<TaskPortfolioEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const needsProject = ready && currentSlug === null;

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setProjects([]);
    adminApi
      .getTaskPortfolio()
      .then((portfolio) => {
        if (!cancelled) setProjects(portfolio.projects);
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
    <div className="mx-auto max-w-6xl space-y-6" data-testid="coordinator-portfolio-page">
      <div>
        <h1 className="m-0 font-[family-name:var(--font-heading)] text-2xl font-bold">
          Tasks across your projects
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Fanned out server-side over the projects you belong to; the selected project leads.
        </p>
      </div>

      {!needsProject && error !== '' && (
        <div
          role="alert"
          data-testid="portfolio-error"
          className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      {needsProject ? (
        <EmptyState
          icon={Inbox}
          title="Select a project"
          description="The Task portfolio is scoped to one project."
        />
      ) : loading ? (
        <div aria-label="Loading portfolio">
          <Skeleton className="h-40 w-full" />
        </div>
      ) : projects.length === 0 && error === '' ? (
        <EmptyState
          icon={Inbox}
          title="No Tasks yet"
          description="Create the first Task from the board."
        />
      ) : (
        projects.map((entry) => (
          <Card key={entry.project_id} data-testid={`portfolio-project-${entry.project_id}`}>
            <CardHeader className="border-b">
              <CardTitle>
                <h2 className="m-0 text-base font-medium">{entry.project_id}</h2>
              </CardTitle>
              <CardDescription className="text-foreground">
                Showing {entry.tasks.length} of this project's Tasks.
              </CardDescription>
              <CardAction>
                <Badge variant={entry.needs_you > 0 ? 'default' : 'secondary'}>
                  {entry.needs_you} need you
                </Badge>
              </CardAction>
            </CardHeader>
            <CardContent>
              <ResponsiveTable
                columns={[
                  { key: 'task', label: 'Task' },
                  { key: 'status', label: 'Status' },
                  { key: 'column', label: 'Column' },
                  { key: 'updated', label: 'Updated' },
                ]}
                rows={entry.tasks.map((task) => ({
                  task: (
                    <Link
                      href={`/tasks/${encodeURIComponent(task.task_id)}`}
                      className="font-medium text-primary no-underline hover:underline"
                    >
                      {task.title}
                    </Link>
                  ),
                  status: (
                    <Badge variant={statusVariant(task.status)}>{displayLabel(task.status)}</Badge>
                  ),
                  column: <span className="capitalize">{displayLabel(task.board_column)}</span>,
                  updated: <span>{formatMoment(task.updated_at)}</span>,
                }))}
                emptyMessage="No Tasks in this project."
              />
            </CardContent>
          </Card>
        ))
      )}
    </div>
  );
}
