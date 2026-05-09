'use client';

import { memo, useMemo } from 'react';
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type { AutopilotAgentGraphJSON } from '@/utils/types';
import { layoutAgentGraph, type AgentNodeData } from './agent-graph-layout';

const KIND_BADGE: Record<AgentNodeData['kind'], string> = {
  llm: 'bg-primary/10 text-primary border-primary/30',
  deterministic: 'bg-success/10 text-success border-success/30',
};

function AgentNodeComponent({ data }: NodeProps) {
  const node = data as AgentNodeData;
  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-border !w-2 !h-2" />
      <div
        className="rounded-lg border border-border bg-bg-surface px-3 py-2 shadow-sm min-w-[200px]"
        title={node.promptRef ? `Prompt: ${node.promptRef}` : undefined}
        data-testid="agent-graph-node"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-semibold text-sm text-text-primary truncate">
            {node.role}
          </span>
          <span
            className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${KIND_BADGE[node.kind]}`}
          >
            {node.kind}
          </span>
        </div>
        {node.tools.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {node.tools.map((t) => (
              <span
                key={t}
                className="rounded bg-bg-subtle text-text-secondary text-[10px] px-1.5 py-0.5 font-[family-name:var(--font-mono)]"
              >
                {t}
              </span>
            ))}
          </div>
        )}
        {node.promptRef && (
          <div className="mt-1 text-[10px] text-text-muted font-[family-name:var(--font-mono)] truncate">
            {node.promptRef}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-border !w-2 !h-2" />
    </>
  );
}

const AgentNode = memo(AgentNodeComponent);

const nodeTypes = { agentNode: AgentNode };

export function AutopilotAgentGraph({ graph }: { graph: AutopilotAgentGraphJSON }) {
  const layout = useMemo(() => layoutAgentGraph(graph), [graph]);

  if (layout.nodes.length === 0) {
    return (
      <div
        className="flex h-64 items-center justify-center text-sm text-text-secondary"
        data-testid="agent-graph-empty"
      >
        No agents declared in this blueprint.
      </div>
    );
  }

  return (
    <div className="h-[480px] w-full" data-testid="agent-graph">
      <ReactFlow
        nodes={layout.nodes}
        edges={layout.edges}
        nodeTypes={nodeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
