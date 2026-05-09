import dagre from 'dagre';
import type { Edge, Node } from '@xyflow/react';
import type {
  AutopilotAgentGraphEdge,
  AutopilotAgentGraphJSON,
  AutopilotAgentGraphNode,
} from '@/utils/types';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 96;

export interface AgentNodeData extends Record<string, unknown> {
  role: string;
  kind: 'llm' | 'deterministic';
  tools: string[];
  promptRef: string | null;
}

export function layoutAgentGraph(
  graph: AutopilotAgentGraphJSON,
): { nodes: Node<AgentNodeData>[]; edges: Edge[] } {
  if (graph.nodes.length === 0) return { nodes: [], edges: [] };

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 80 });

  for (const n of graph.nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const e of graph.edges) {
    g.setEdge(e.from, e.to);
  }
  dagre.layout(g);

  const nodes: Node<AgentNodeData>[] = graph.nodes.map((n: AutopilotAgentGraphNode) => {
    const p = g.node(n.id);
    return {
      id: n.id,
      type: 'agentNode',
      position: { x: p.x - NODE_WIDTH / 2, y: p.y - NODE_HEIGHT / 2 },
      data: {
        role: n.role,
        kind: n.kind,
        tools: n.tools,
        promptRef: n.prompt_ref,
      },
    };
  });

  const edges: Edge[] = graph.edges.map((e: AutopilotAgentGraphEdge, i: number) => ({
    id: `${e.from}->${e.to}-${i}`,
    source: e.from,
    target: e.to,
    label: e.label,
    animated: false,
  }));

  return { nodes, edges };
}
