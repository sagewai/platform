'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  MiniMap,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from '@xyflow/react';
import dagre from 'dagre';
import '@xyflow/react/dist/style.css';

import { CanvasNode, type CanvasNodeData } from './canvas-node';
import { CanvasEdge, type CanvasEdgeData } from './canvas-edge';
import { CanvasSidebar } from './canvas-sidebar';
import type { WorkflowEvent } from '@/utils/types';
import { authSSE } from '@/utils/auth';

const API_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

const nodeTypes = { canvas: CanvasNode };
const edgeTypes = { canvas: CanvasEdge };

interface Props {
  runId: string;
  workflowDefinition?: Record<string, unknown> | null;
  events: WorkflowEvent[];
  isLive: boolean;
}

/* ─── Dagre layout helper ─── */
function layoutGraph(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 100 });

  for (const node of nodes) {
    g.setNode(node.id, { width: 180, height: 80 });
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: { x: pos.x - 90, y: pos.y - 40 },
    };
  });

  return { nodes: layoutedNodes, edges };
}

/* ─── Extract agents from workflow definition or events ─── */
function extractAgents(
  def?: Record<string, unknown> | null,
  events?: WorkflowEvent[],
): string[] {
  // Try from definition agents map
  if (def) {
    const agents = def.agents as Record<string, unknown> | undefined;
    if (agents && typeof agents === 'object') {
      return Object.keys(agents);
    }
  }

  // Fallback: extract from events
  if (events && events.length > 0) {
    const names = new Set<string>();
    for (const evt of events) {
      const agent =
        (evt.data?.agent as string) ??
        (evt.data?.agent_name as string);
      if (agent) names.add(agent);
    }
    if (names.size > 0) return Array.from(names);
  }

  return [];
}

/* ─── Map events to node data ─── */
function buildNodeStates(
  agents: string[],
  events: WorkflowEvent[],
  def?: Record<string, unknown> | null,
): Map<string, CanvasNodeData> {
  const states = new Map<string, CanvasNodeData>();

  // Initialize all agents as pending
  for (const name of agents) {
    const agentDef =
      def?.agents && typeof def.agents === 'object'
        ? (def.agents as Record<string, Record<string, unknown>>)[name]
        : undefined;

    states.set(name, {
      label: name,
      status: 'pending',
      model: (agentDef?.model as string) ?? undefined,
      strategy: (agentDef?.strategy as string) ?? undefined,
    });
  }

  // Apply events in order
  for (const evt of events) {
    const agent =
      (evt.data?.agent as string) ??
      (evt.data?.agent_name as string);
    if (!agent) continue;

    const current = states.get(agent) ?? {
      label: agent,
      status: 'pending' as const,
    };

    switch (evt.event_type) {
      case 'step_started':
        states.set(agent, { ...current, status: 'running' });
        break;
      case 'step_completed':
        states.set(agent, {
          ...current,
          status: 'completed',
          tokens: (evt.data?.total_tokens as number) ?? current.tokens,
          output: (evt.data?.output as string) ?? current.output,
          duration: (evt.data?.duration as number) ?? current.duration,
          model: (evt.data?.model as string) ?? current.model,
        });
        break;
      case 'workflow_failed':
        // Mark any running agent as failed
        for (const [key, val] of states) {
          if (val.status === 'running') {
            states.set(key, {
              ...val,
              status: 'failed',
              error: (evt.data?.error as string) ?? 'Unknown error',
            });
          }
        }
        break;
    }
  }

  return states;
}

function ExecutionCanvasInner({ runId, workflowDefinition, events, isLive }: Props) {
  const [selectedNode, setSelectedNode] = useState<CanvasNodeData | null>(null);

  const [liveEvents, setLiveEvents] = useState<WorkflowEvent[]>([]);

  const allEvents = isLive ? [...events, ...liveEvents] : events;
  const agents = useMemo(
    () => extractAgents(workflowDefinition, allEvents),
    [workflowDefinition, allEvents],
  );

  const nodeStates = useMemo(
    () => buildNodeStates(agents, allEvents, workflowDefinition),
    [agents, allEvents, workflowDefinition],
  );

  // Build initial nodes and edges (respecting parallel/sequential structure)
  const { initialNodes, initialEdges } = useMemo(() => {
    const rawNodes: Node[] = agents.map((name) => {
      const data = nodeStates.get(name) ?? { label: name, status: 'pending' as const };
      return {
        id: name,
        type: 'canvas',
        position: { x: 0, y: 0 },
        data,
      };
    });

    function makeEdge(source: string, target: string): Edge {
      const srcState = nodeStates.get(source);
      const edgeStatus =
        srcState?.status === 'completed'
          ? 'completed'
          : srcState?.status === 'running'
            ? 'active'
            : 'idle';
      return {
        id: `${source}-${target}`,
        source,
        target,
        type: 'canvas',
        data: {
          animated: edgeStatus === 'active',
          status: edgeStatus,
        } satisfies CanvasEdgeData,
      };
    }

    // Walk workflow tree to extract edges that respect parallel structure.
    // Returns the entry and exit agent names for each sub-tree so parent
    // nodes can wire them together.
    type Terminals = { entries: string[]; exits: string[] };

    function walkNode(node: Record<string, unknown>): Terminals {
      // AgentStep: { agent: "name" }
      if (typeof node.agent === 'string' && !node.type) {
        return { entries: [node.agent], exits: [node.agent] };
      }
      // SequentialNode: { type: "sequential", steps: [...] }
      if (node.type === 'sequential' && Array.isArray(node.steps)) {
        let prev: Terminals | null = null;
        for (const step of node.steps) {
          const cur = walkNode(step as Record<string, unknown>);
          if (prev) {
            // Connect every exit of prev to every entry of cur
            for (const ex of prev.exits) {
              for (const en of cur.entries) {
                rawEdges.push(makeEdge(ex, en));
              }
            }
          }
          prev = cur;
        }
        if (!prev) return { entries: [], exits: [] };
        const first = walkNode(node.steps[0] as Record<string, unknown>);
        return { entries: first.entries, exits: prev.exits };
      }
      // ParallelNode: { type: "parallel", agents: ["a", "b"] }
      if (node.type === 'parallel' && Array.isArray(node.agents)) {
        return {
          entries: node.agents as string[],
          exits: node.agents as string[],
        };
      }
      // LoopNode: { type: "loop", agent: "name" }
      if (node.type === 'loop' && typeof node.agent === 'string') {
        return { entries: [node.agent], exits: [node.agent] };
      }
      return { entries: [], exits: [] };
    }

    const rawEdges: Edge[] = [];

    // Try structured edge building from workflow definition
    const wfNode = workflowDefinition?.workflow as Record<string, unknown> | undefined;
    if (wfNode) {
      walkNode(wfNode);
    } else {
      // Fallback: sequential chain
      for (let i = 0; i < agents.length - 1; i++) {
        rawEdges.push(makeEdge(agents[i], agents[i + 1]));
      }
    }

    const result = layoutGraph(rawNodes, rawEdges);
    return { initialNodes: result.nodes, initialEdges: result.edges };
  }, [agents, nodeStates]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Sync when initialNodes/initialEdges change
  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  // SSE subscription for live mode (via authSSE, not EventSource)
  useEffect(() => {
    if (!isLive) return;

    const controller = authSSE(
      `${API_BASE}/workflows/runs/${runId}/events`,
      (type, data) => {
        setLiveEvents((prev) => [
          ...prev,
          {
            id: prev.length,
            run_id: runId,
            event_type: type,
            data,
            created_at: new Date().toISOString(),
          },
        ]);

        if (['workflow_finished', 'workflow_failed', 'workflow_cancelled'].includes(type)) {
          controller.abort();
        }
      },
    );

    return () => controller.abort();
  }, [isLive, runId]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const data = nodeStates.get(node.id);
      setSelectedNode(data ?? null);
    },
    [nodeStates],
  );

  return (
    <div className="relative w-full h-[500px] bg-bg-subtle rounded-lg border border-border overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        maxZoom={2}
      >
        <MiniMap
          nodeColor={(n) => {
            const d = n.data as unknown as CanvasNodeData;
            switch (d?.status) {
              case 'completed':
                return '#22c55e';
              case 'failed':
                return '#ef4444';
              case 'running':
                return '#26c6da';
              case 'waiting':
                return '#f59e0b';
              default:
                return '#6b7280';
            }
          }}
          className="!bg-[#0a1628]"
        />
        <Controls className="!bg-bg-surface !border-border !shadow-sm [&_button]:!bg-bg-surface [&_button]:!border-border [&_button]:!text-text-primary" />
      </ReactFlow>
      <CanvasSidebar data={selectedNode} onClose={() => setSelectedNode(null)} />
    </div>
  );
}

export function ExecutionCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <ExecutionCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
