'use client';

import { memo, useMemo } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  type NodeTypes,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type { AutopilotAgentGraphJSON } from '@/utils/types';
import { layoutAgentGraph } from './agent-graph-layout';
import { AnimatedAgentNode } from './animated-agent-node';
import { DataFlowParticles } from './data-flow-particles';

const nodeTypes: NodeTypes = { agentNode: AnimatedAgentNode };

function AnimatedAgentGraphComponent({ graph }: { graph: AutopilotAgentGraphJSON }) {
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
    <div className="relative h-[480px] w-full" data-testid="agent-graph">
      <ReactFlow
        nodes={layout.nodes}
        edges={layout.edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        edgesFocusable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
      <DataFlowParticles edges={layout.edges} />
    </div>
  );
}

export const AnimatedAgentGraph = memo(AnimatedAgentGraphComponent);
