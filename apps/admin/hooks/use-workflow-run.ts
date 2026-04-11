'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { adminApi } from '../utils/api';
import { authSSE } from '../utils/auth';
import type { WorkflowRunStatus } from '../utils/types';

const ANALYTICS_URL = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

export interface WorkflowRunEvent {
  type: string;
  data: Record<string, unknown>;
  timestamp: number;
}

interface UseWorkflowRunReturn {
  status: WorkflowRunStatus | null;
  events: WorkflowRunEvent[];
  output: Record<string, unknown> | null;
  error: string | null;
  stepsCompleted: number;
  stepsTotal: number | null;
  isTerminal: boolean;
  cancel: () => Promise<void>;
}

const TERMINAL_STATUSES: WorkflowRunStatus[] = ['completed', 'failed', 'cancelled'];

/**
 * React hook for tracking a durable workflow run.
 *
 * 1. Connects to the SSE endpoint via authSSE (sends JWT).
 * 2. Falls back to polling if SSE stream fails.
 * 3. Auto-cleans up on unmount (workflow keeps running on server).
 */
export function useWorkflowRun(runId: string | null): UseWorkflowRunReturn {
  const [status, setStatus] = useState<WorkflowRunStatus | null>(null);
  const [events, setEvents] = useState<WorkflowRunEvent[]>([]);
  const [output, setOutput] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stepsCompleted, setStepsCompleted] = useState(0);
  const [stepsTotal, setStepsTotal] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isTerminal = status != null && TERMINAL_STATUSES.includes(status);

  const cancel = useCallback(async () => {
    if (!runId) return;
    await adminApi.cancelWorkflowRun(runId);
    setStatus('cancelled');
  }, [runId]);

  // Clean up helper
  const cleanup = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!runId) {
      setStatus(null);
      setEvents([]);
      setOutput(null);
      setError(null);
      setStepsCompleted(0);
      setStepsTotal(null);
      return;
    }

    // Reset state for new run
    setStatus('pending');
    setEvents([]);
    setOutput(null);
    setError(null);
    setStepsCompleted(0);

    function startPolling(id: string) {
      if (pollIntervalRef.current) return; // prevent duplicate polling

      const poll = async () => {
        try {
          const run = await adminApi.getWorkflowRun(id);
          setStatus(run.status);
          setStepsCompleted(run.steps_completed);
          setStepsTotal(run.steps_total);

          if (run.status === 'completed') {
            // Normalize: backend may return output as a raw string or object
            const rawOutput = run.output;
            const normalized = rawOutput == null
              ? null
              : typeof rawOutput === 'string'
                ? { output: rawOutput }
                : (rawOutput as Record<string, unknown>);
            setOutput(normalized);
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current);
              pollIntervalRef.current = null;
            }
          } else if (run.status === 'failed') {
            setError(run.error || 'Unknown error');
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current);
              pollIntervalRef.current = null;
            }
          } else if (run.status === 'cancelled') {
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current);
              pollIntervalRef.current = null;
            }
          }
        } catch {
          // Ignore poll errors
        }
      };

      poll();
      pollIntervalRef.current = setInterval(poll, 2000);
    }

    const sseUrl = `${ANALYTICS_URL}/workflows/runs/${encodeURIComponent(runId)}/events`;
    const controller = authSSE(
      sseUrl,
      (eventType, data) => {
        const event: WorkflowRunEvent = {
          type: eventType,
          data: typeof data === 'object' ? data : { _raw: data },
          timestamp: Date.now(),
        };
        setEvents((prev) => [...prev, event]);

        if (eventType === 'step_started' || eventType === 'workflow_started') {
          setStatus('running');
        } else if (eventType === 'step_completed') {
          setStepsCompleted((prev) => prev + 1);
        } else if (eventType === 'workflow_finished') {
          setStatus('completed');
          // Normalize: SSE data may be a raw string or { output: "..." }
          const normalized = typeof data === 'string'
            ? { output: data }
            : data;
          setOutput(normalized);
        } else if (eventType === 'workflow_failed') {
          setStatus('failed');
          setError((data.error as string) || 'Unknown error');
        } else if (eventType === 'workflow_cancelled') {
          setStatus('cancelled');
        }
      },
      { onError: () => startPolling(runId) },
    );
    abortRef.current = controller;

    return cleanup;
  }, [runId, cleanup]);

  return {
    status,
    events,
    output,
    error,
    stepsCompleted,
    stepsTotal,
    isTerminal,
    cancel,
  };
}
