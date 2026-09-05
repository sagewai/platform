'use client';

import { useEffect, useState } from 'react';

import { authSSE } from '@/utils/auth';
import { adminApi, taskScopeHeaders } from '@/utils/api';
import type { TaskFeedEntry } from '@/utils/types';

export interface TaskFeed {
  /** Increments on every Task event, so a page re-reads its projection instead of folding. */
  revision: number;
  /** The last connection error, or the empty string while the stream is healthy. */
  error: string;
}

/**
 * Subscribe to one Task's durable feed.
 *
 * `enabled` is the caller's project scope, not `useProject().ready`: `taskScopeHeaders()`
 * throws when no project is selected, and an effect body is the one place a throw has nowhere
 * to go. Pages pass `currentSlug !== null`.
 *
 * The hook folds nothing. Every projection the console renders — the thread, the actions, the
 * plan — is a route, and a frame only says one of them moved.
 */
export function useTaskFeed(taskId: string, enabled: boolean): TaskFeed {
  const [feed, setFeed] = useState<TaskFeed>({ revision: 0, error: '' });

  useEffect(() => {
    if (!enabled) return;

    setFeed({ revision: 0, error: '' });

    const controller = authSSE(
      adminApi.taskEventsUrl(taskId),
      (_event, data) => {
        setFeed((current) => (current.error === '' ? current : { ...current, error: '' }));
        const entry = data as unknown as TaskFeedEntry;
        if (entry.source !== 'task_event') return;
        setFeed((current) => ({ revision: current.revision + 1, error: '' }));
      },
      {
        reconnect: true,
        headers: taskScopeHeaders(),
        onError: () =>
          setFeed((current) => ({ ...current, error: 'The Task feed dropped; reconnecting.' })),
      },
    );

    return () => controller.abort();
  }, [taskId, enabled]);

  return feed;
}
