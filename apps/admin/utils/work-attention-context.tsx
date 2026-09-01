'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { PendingAttention } from '@/utils/types';

interface WorkAttentionContextValue {
  pending: PendingAttention[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const WorkAttentionContext = createContext<WorkAttentionContextValue>({
  pending: [],
  loading: true,
  error: null,
  refresh: async () => {},
});

function attentionMessage(item: PendingAttention): string {
  if (item.kind === 'GATE_REQUESTED') {
    return `Approval needed: ${item.summary}`;
  }
  if (item.kind === 'WORK_BLOCKED') {
    return `Work blocked: ${item.summary}`;
  }
  if (item.kind === 'CONTROL_DEGRADED') {
    return `Work control degraded: ${item.summary}`;
  }
  if (item.kind === 'EXTERNAL_OUTCOME_INCIDENT') {
    return `External outcome incident: ${item.summary}`;
  }
  return `Work needs attention: ${item.summary}`;
}

export function WorkAttentionProvider({ children }: { children: ReactNode }) {
  const { currentSlug, ready } = useProject();
  const { toast } = useToast();
  const [pending, setPending] = useState<PendingAttention[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const scope = currentSlug ?? 'global';
  const scopeRef = useRef(scope);
  const requestGeneration = useRef(0);
  const notifiedIds = useRef(new Set<string>());
  scopeRef.current = scope;

  const refresh = useCallback(async () => {
    if (!ready) return;
    const generation = ++requestGeneration.current;
    const requestedScope = scope;
    try {
      const items = await adminApi.listPendingWorkAttention();
      if (
        requestGeneration.current !== generation
        || scopeRef.current !== requestedScope
      ) return;

      for (const item of items) {
        if (notifiedIds.current.has(item.attention_id)) continue;
        toast(
          item.kind === 'GATE_REQUESTED' ? 'info' : 'error',
          attentionMessage(item),
        );
        notifiedIds.current.add(item.attention_id);
      }
      setPending(items);
      setError(null);
    } catch {
      if (
        requestGeneration.current === generation
        && scopeRef.current === requestedScope
      ) {
        setError('Failed to load pending Work attention.');
      }
    } finally {
      if (
        requestGeneration.current === generation
        && scopeRef.current === requestedScope
      ) {
        setLoading(false);
      }
    }
  }, [ready, scope, toast]);

  useEffect(() => {
    requestGeneration.current += 1;
    setPending([]);
    setError(null);
    if (!ready) {
      setLoading(true);
      return;
    }
    setLoading(true);
    void refresh();

    const interval = window.setInterval(() => {
      void refresh();
    }, 15_000);
    const onFocus = () => {
      void refresh();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refresh();
      }
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      requestGeneration.current += 1;
      window.clearInterval(interval);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [ready, refresh]);

  return (
    <WorkAttentionContext.Provider value={{ pending, loading, error, refresh }}>
      {children}
    </WorkAttentionContext.Provider>
  );
}

export function useWorkAttention() {
  return useContext(WorkAttentionContext);
}
