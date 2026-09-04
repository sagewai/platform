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
import { getCurrentProjectId } from '@/utils/project-state';
import type { PendingAttention, TaskDecisionItem } from '@/utils/types';

interface WorkAttentionContextValue {
  pending: PendingAttention[];
  /**
   * What the nav badge counts: Work attention plus the Task half of the project's `Needs you`
   * inbox. The inbox's own `work` items are the same `pending_attention` rows already in
   * `pending`. A mirrored Work gate appears in `pending` and the inbox's Task half, so the
   * pending row is skipped when its `attention_id` matches the mirrored gate.
   * The client does not request the inbox without a selected project because Tasks are never
   * organization-global.
   */
  attentionItems: Array<PendingAttention | TaskDecisionItem>;
  /** The whole `Needs you` inbox, Work half included; what `/decisions` renders. */
  decisions: TaskDecisionItem[];
  loading: boolean;
  error: string | null;
  /** The inbox's own failure. It never becomes `error`: the Work page renders that one. */
  decisionsError: string | null;
  refresh: () => Promise<void>;
}

const WorkAttentionContext = createContext<WorkAttentionContextValue>({
  pending: [],
  attentionItems: [],
  decisions: [],
  loading: true,
  error: null,
  decisionsError: null,
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
  const [decisions, setDecisions] = useState<TaskDecisionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [decisionsError, setDecisionsError] = useState<string | null>(null);
  const requestGeneration = useRef(0);
  const notifiedIds = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    if (!ready) return;
    const generation = ++requestGeneration.current;
    const requestedProjectId = getCurrentProjectId();
    const [work, inbox] = await Promise.allSettled([
      adminApi.listPendingWorkAttention(),
      requestedProjectId === null
        ? Promise.resolve({ items: [] as TaskDecisionItem[] })
        : adminApi.listTaskDecisions(),
    ]);
    if (requestGeneration.current !== generation || getCurrentProjectId() !== requestedProjectId) return;

    if (work.status === 'fulfilled') {
      for (const item of work.value) {
        if (notifiedIds.current.has(item.attention_id)) continue;
        toast(item.kind === 'GATE_REQUESTED' ? 'info' : 'error', attentionMessage(item));
        notifiedIds.current.add(item.attention_id);
      }
      setPending(work.value);
      setError(null);
    } else {
      setError('Failed to load pending Work attention.');
    }

    setDecisions(inbox.status === 'fulfilled' ? inbox.value.items : []);
    setDecisionsError(
      inbox.status === 'fulfilled'
        ? null
        : inbox.reason instanceof Error ? inbox.reason.message : String(inbox.reason),
    );
    setLoading(false);
  }, [ready, toast]);

  useEffect(() => {
    requestGeneration.current += 1;
    setPending([]);
    setDecisions([]);
    setError(null);
    setDecisionsError(null);
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
  }, [ready, currentSlug, refresh]);

  const taskDecisions = decisions.filter((item) => item.kind === 'task');
  const mirroredGateIds = new Set(
    taskDecisions
      .filter((item) => item.decided_by === 'work')
      .map((item) => item.attention_id),
  );
  const badgePending = pending.filter((item) => !mirroredGateIds.has(item.attention_id));

  return (
    <WorkAttentionContext.Provider
      value={{
        pending,
        attentionItems: [...badgePending, ...taskDecisions],
        decisions,
        loading,
        error,
        decisionsError,
        refresh,
      }}
    >
      {children}
    </WorkAttentionContext.Provider>
  );
}

export function useWorkAttention() {
  return useContext(WorkAttentionContext);
}
