'use client';

import { use, useEffect, useRef, useState } from 'react';
import { Activity } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { useTaskFeed } from '@/hooks/use-task-feed';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { ActivitySource, OperatorActivity, TaskActivityPage } from '@/utils/types';

const SOURCES: ActivitySource[] = ['codex', 'claude', 'harness', 'verifier', 'coordinator'];

interface ActivityFilters {
  source: ActivitySource | '';
  workId: string;
  runId: string;
}

const EMPTY_FILTERS: ActivityFilters = { source: '', workId: '', runId: '' };

function ActivityErrorLine({ message }: { message: string }) {
  return (
    <p role="alert" data-testid="task-activity-error" className="m-0 text-sm text-foreground">
      {message}
    </p>
  );
}

function usageLine(item: OperatorActivity): string {
  const parts: string[] = [];
  if (item.input_tokens !== null) parts.push(`${item.input_tokens} in`);
  if (item.output_tokens !== null) parts.push(`${item.output_tokens} out`);
  if (item.cost_usd !== null) parts.push(`$${item.cost_usd.toFixed(2)}`);
  return parts.join(' / ');
}

async function readActivity(
  taskId: string,
  filters: ActivityFilters,
  cursor: string | null,
): Promise<TaskActivityPage> {
  return adminApi.listTaskActivity(taskId, {
    source: filters.source === '' ? undefined : filters.source,
    work_id: filters.workId === '' ? undefined : filters.workId,
    run_id: filters.runId === '' ? undefined : filters.runId,
    cursor: cursor ?? undefined,
    limit: 200,
  });
}

export default function TaskActivityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { currentSlug, ready } = useProject();
  const feed = useTaskFeed(id, ready && currentSlug !== null);
  const [items, setItems] = useState<OperatorActivity[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [source, setSource] = useState<ActivitySource | ''>('');
  const [workId, setWorkId] = useState('');
  const [runId, setRunId] = useState('');
  const [filters, setFilters] = useState<ActivityFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState(0);
  const [error, setError] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);
  const requestKey = `${currentSlug ?? ''}:${id}:${applied}`;
  const requestKeyRef = useRef(requestKey);
  requestKeyRef.current = requestKey;
  const shownKeyRef = useRef('');

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    if (shownKeyRef.current !== requestKeyRef.current) {
      shownKeyRef.current = requestKeyRef.current;
      setItems(null);
      setCursor(null);
    }
    setError('');
    readActivity(id, filters, null)
      .then((page) => {
        if (!cancelled) {
          setItems(page.items);
          setCursor(page.next_cursor);
        }
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, id, feed.revision, applied, filters]);

  async function loadMore() {
    if (cursor === null) return;
    const issuedKey = requestKey;
    setLoadingMore(true);
    setError('');
    try {
      const page = await readActivity(id, filters, cursor);
      if (requestKeyRef.current !== issuedKey) return;
      setItems((current) => (current === null ? page.items : [...current, ...page.items]));
      setCursor(page.next_cursor);
    } catch (cause) {
      if (requestKeyRef.current === issuedKey) setError((cause as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }

  function downloadLoaded(loaded: OperatorActivity[]) {
    const blob = new Blob([JSON.stringify(loaded, null, 2)], { type: 'application/json' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = `${id}-activity.json`;
    anchor.click();
    URL.revokeObjectURL(href);
  }

  return (
    <Card>
      <CardHeader className="border-b">
        <CardTitle>
          <h2 className="m-0 text-base font-medium">Activity</h2>
        </CardTitle>
        <CardDescription className="text-foreground">
          What the operators actually did, across the planning and step Works of this Task, ordered by Work,
          run and sequence. The download carries exactly what is loaded here.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs">
            Source
            <select
              className="h-8 rounded-lg border border-input bg-background px-2 text-sm"
              value={source}
              onChange={(event) => setSource(event.target.value as ActivitySource | '')}
            >
              <option value="">Any source</option>
              {SOURCES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            Work id
            <Input
              className="w-56"
              value={workId}
              onChange={(event) => setWorkId(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            Run id
            <Input
              className="w-56"
              value={runId}
              onChange={(event) => setRunId(event.target.value)}
            />
          </label>
          <Button
            variant="outline"
            onClick={() => {
              setFilters({ source, workId, runId });
              setApplied((count) => count + 1);
            }}
          >
            Apply filters
          </Button>
          <Button
            variant="ghost"
            disabled={items === null || items.length === 0}
            onClick={() => {
              if (items !== null) downloadLoaded(items);
            }}
          >
            Download loaded activity
          </Button>
        </div>

        {items === null && error === '' && (
          <Skeleton aria-label="Loading Task activity" className="h-40 w-full" />
        )}
        {items !== null && items.length === 0 && error === '' && (
          <EmptyState
            icon={Activity}
            title="No activity"
            description="No operator has written a line for this Task under these filters."
            className="border-0 py-8"
          />
        )}
        {items !== null && items.length > 0 && (
          <ol className="m-0 list-none space-y-2 p-0">
            {items.map((item) => {
              const usage = usageLine(item);
              return (
                <li
                  key={`${item.work_id}-${item.run_id}-${item.sequence}`}
                  data-testid={`activity-row-${item.work_id}-${item.run_id}-${item.sequence}`}
                  className="rounded-lg border border-border p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="flex items-center gap-1.5">
                      <Badge variant="outline">{item.source}</Badge>
                      <Badge variant="outline">{item.kind}</Badge>
                      <span className="font-mono">{item.run_id}</span>
                    </span>
                    <span>{new Date(item.at).toLocaleString()}</span>
                  </div>
                  <p className="m-0 mt-1 whitespace-pre-wrap text-sm">{item.summary}</p>
                  {item.detail !== null && (
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs">
                      {item.detail}
                    </pre>
                  )}
                  {usage !== '' && <p className="m-0 mt-1 text-xs">{usage}</p>}
                </li>
              );
            })}
          </ol>
        )}

        {error !== '' && <ActivityErrorLine message={error} />}
        {cursor !== null && (
          <Button
            aria-busy={loadingMore}
            variant="outline"
            disabled={loadingMore}
            onClick={() => void loadMore()}
          >
            {loadingMore ? 'Loading more activity' : 'Load more'}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
