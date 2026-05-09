'use client';

import { useMemo } from 'react';
import { useMissionEventHistory } from '@/lib/mission-events/provider';
import type { MissionRunEvent } from '@/utils/types';

const W = 80;
const H = 24;
const BAR_W = 4;
const GAP = 2;

interface LlmCall {
  tokensIn: number;
  tokensOut: number;
}

function isLlmCall(e: MissionRunEvent): e is MissionRunEvent {
  return e.kind === 'agent.llm_call';
}

export function AgentTokenMiniChart({ agentId }: { agentId: string }) {
  const history = useMissionEventHistory();
  const calls = useMemo<LlmCall[]>(() => {
    const out: LlmCall[] = [];
    for (const e of history) {
      const owner = e.node_id ?? e.agent_id;
      if (owner !== agentId) continue;
      if (!isLlmCall(e)) continue;
      out.push({
        tokensIn: e.input_tokens ?? 0,
        tokensOut: e.output_tokens ?? 0,
      });
    }
    return out;
  }, [history, agentId]);

  const max = calls.reduce(
    (m, c) => Math.max(m, c.tokensIn, c.tokensOut),
    1,
  );
  const slot = BAR_W * 2 + GAP;
  const capacity = Math.max(1, Math.floor(W / slot));
  const visible = calls.slice(-capacity);

  if (calls.length === 0) {
    return (
      <svg
        width={W}
        height={H}
        role="img"
        aria-label={`${agentId} token usage sparkline (empty)`}
        data-testid="agent-token-mini-chart"
        data-call-count={0}
        className="opacity-40"
      >
        <line
          x1={0}
          y1={H - 1}
          x2={W}
          y2={H - 1}
          className="stroke-border"
          strokeWidth={1}
        />
      </svg>
    );
  }

  return (
    <svg
      width={W}
      height={H}
      role="img"
      aria-label={`${agentId} token usage — ${calls.length} call${calls.length === 1 ? '' : 's'}`}
      data-testid="agent-token-mini-chart"
      data-call-count={calls.length}
    >
      {visible.map((c, i) => {
        const x = i * slot;
        const hIn = (c.tokensIn / max) * (H - 1);
        const hOut = (c.tokensOut / max) * (H - 1);
        return (
          <g key={i}>
            <rect
              data-token-bar="in"
              x={x}
              y={H - hIn}
              width={BAR_W}
              height={hIn}
              className="fill-primary/70"
            />
            <rect
              data-token-bar="out"
              x={x + BAR_W}
              y={H - hOut}
              width={BAR_W}
              height={hOut}
              className="fill-success/70"
            />
          </g>
        );
      })}
    </svg>
  );
}
