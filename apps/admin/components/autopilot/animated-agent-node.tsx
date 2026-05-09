'use client';

import { memo, useMemo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { motion, useReducedMotion } from 'framer-motion';
import { Check, AlertTriangle } from 'lucide-react';
import { useMissionEventHistory } from '@/lib/mission-events/provider';
import type { MissionRunEvent } from '@/utils/types';
import type { AgentNodeData } from './agent-graph-layout';

type AnimState = 'idle' | 'active' | 'completed' | 'failed';

const STATE_RING: Record<AnimState, string> = {
  idle: 'border-border',
  active: 'border-primary ring-2 ring-primary/40',
  completed: 'border-success ring-2 ring-success/30',
  failed: 'border-error ring-2 ring-error/30',
};

const KIND_BADGE: Record<AgentNodeData['kind'], string> = {
  llm: 'bg-primary/10 text-primary border-primary/30',
  deterministic: 'bg-success/10 text-success border-success/30',
};

// Failed is sticky — once a tool fails for this node, only an explicit
// agent.finished with status='completed' can reset it. agent.finished without
// a status field (the common Plan H shape) is treated as success unless we
// already saw a failure.
function deriveState(events: readonly MissionRunEvent[]): AnimState {
  let sawFailure = false;
  let touched = false;
  let finished = false;
  for (const e of events) {
    if (e.kind === 'agent.tool_failed') sawFailure = true;
    if (
      e.kind === 'agent.started' ||
      e.kind === 'agent.tool_call' ||
      e.kind === 'agent.llm_call'
    ) {
      touched = true;
    }
    if (e.kind === 'agent.finished') {
      finished = true;
      if (e.status === 'failed') sawFailure = true;
    }
  }
  if (finished) return sawFailure ? 'failed' : 'completed';
  if (sawFailure) return 'failed';
  if (touched) return 'active';
  return 'idle';
}

interface AnimatedAgentNodeProps extends NodeProps {
  data: AgentNodeData;
}

function AnimatedAgentNodeComponent({ id, data }: AnimatedAgentNodeProps) {
  const reduced = useReducedMotion();
  const history = useMissionEventHistory();

  const state: AnimState = useMemo(() => {
    const mine = history.filter((e) => e.node_id === id || e.agent_id === id);
    return deriveState(mine);
  }, [history, id]);

  const pulseAnim = !reduced && state === 'active'
    ? {
        scale: [1, 1.03, 1],
        boxShadow: [
          '0 0 0 0 rgba(14, 116, 144, 0.45)',
          '0 0 0 10px rgba(14, 116, 144, 0)',
          '0 0 0 0 rgba(14, 116, 144, 0)',
        ],
      }
    : undefined;

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-border !w-2 !h-2" />
      <motion.div
        data-state={state}
        data-testid="agent-graph-node"
        layoutId={`agent-${id}`}
        animate={pulseAnim}
        transition={{
          duration: 1.6,
          repeat: state === 'active' && !reduced ? Infinity : 0,
          ease: 'easeInOut',
        }}
        className={`rounded-lg border bg-bg-surface px-3 py-2 shadow-sm min-w-[200px] transition-colors ${STATE_RING[state]}`}
        title={data.promptRef ? `Prompt: ${data.promptRef}` : undefined}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-semibold text-sm text-text-primary truncate flex items-center gap-1.5">
            {data.role}
            {state === 'completed' && (
              <motion.span
                initial={reduced ? { opacity: 1 } : { scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ type: 'spring', stiffness: 280, damping: 14 }}
                aria-hidden
              >
                <Check className="size-4 text-success" />
              </motion.span>
            )}
            {state === 'failed' && (
              <motion.span
                animate={reduced ? {} : { x: [0, -3, 3, -2, 2, 0] }}
                transition={{ duration: 0.4 }}
                aria-hidden
              >
                <AlertTriangle className="size-4 text-error" />
              </motion.span>
            )}
          </span>
          <span
            className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${KIND_BADGE[data.kind]}`}
          >
            {data.kind}
          </span>
        </div>
        {data.tools.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {data.tools.map((t) => (
              <span
                key={t}
                className="rounded bg-bg-subtle text-text-secondary text-[10px] px-1.5 py-0.5 font-[family-name:var(--font-mono)]"
              >
                {t}
              </span>
            ))}
          </div>
        )}
        {data.promptRef && (
          <div className="mt-1 text-[10px] text-text-muted font-[family-name:var(--font-mono)] truncate">
            {data.promptRef}
          </div>
        )}
      </motion.div>
      <Handle type="source" position={Position.Right} className="!bg-border !w-2 !h-2" />
    </>
  );
}

export const AnimatedAgentNode = memo(AnimatedAgentNodeComponent);
