'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force';
import { scaleLinear } from 'd3-scale';
import { NetworkNode } from './network-node';
import { NetworkDetailPanel } from './network-detail-panel';

interface NodeData {
  id: string;
  tokens: number;
  runs: number;
  error_rate: number;
}

interface EdgeData {
  source: string;
  target: string;
  weight: number;
}

interface AgentNetworkProps {
  nodes: NodeData[];
  edges: EdgeData[];
}

interface SimNode extends SimulationNodeDatum {
  id: string;
  tokens: number;
  runs: number;
  error_rate: number;
}

interface SimLink extends SimulationLinkDatum<SimNode> {
  weight: number;
}

const WIDTH = 800;
const HEIGHT = 500;

const sizeScale = scaleLinear().domain([0, 1]).range([20, 50]).clamp(true);

const edgeWidthScale = scaleLinear().domain([1, 100]).range([1, 6]).clamp(true);

export function AgentNetwork({ nodes, edges }: AgentNetworkProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [simNodes, setSimNodes] = useState<SimNode[]>([]);
  const [simLinks, setSimLinks] = useState<SimLink[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<EdgeData | null>(null);
  const dragRef = useRef<{ id: string; offsetX: number; offsetY: number } | null>(null);
  const simulationRef = useRef<ReturnType<typeof forceSimulation<SimNode>> | null>(null);

  const maxTokens = useMemo(() => Math.max(...nodes.map((n) => n.tokens), 1), [nodes]);

  // Initialize simulation
  useEffect(() => {
    if (!nodes.length) return;

    const simNodesInit: SimNode[] = nodes.map((n) => ({
      ...n,
      x: WIDTH / 2 + (Math.random() - 0.5) * 200,
      y: HEIGHT / 2 + (Math.random() - 0.5) * 200,
    }));

    const nodeMap = new Map(simNodesInit.map((n) => [n.id, n]));

    const simLinksInit: SimLink[] = edges
      .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
      .map((e) => ({
        source: nodeMap.get(e.source)!,
        target: nodeMap.get(e.target)!,
        weight: e.weight,
      }));

    const sim = forceSimulation<SimNode>(simNodesInit)
      .force(
        'link',
        forceLink<SimNode, SimLink>(simLinksInit)
          .id((d) => d.id)
          .distance(120),
      )
      .force('charge', forceManyBody().strength(-300))
      .force('center', forceCenter(WIDTH / 2, HEIGHT / 2))
      .force(
        'collision',
        forceCollide<SimNode>().radius((d) => {
          const normalized = d.tokens / maxTokens;
          return sizeScale(normalized) + 10;
        }),
      )
      .on('tick', () => {
        setSimNodes([...simNodesInit]);
        setSimLinks([...simLinksInit]);
      });

    simulationRef.current = sim;

    return () => {
      sim.stop();
    };
  }, [nodes, edges, maxTokens]);

  const handleDragStart = useCallback(
    (id: string, e: React.MouseEvent) => {
      e.preventDefault();
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const node = simNodes.find((n) => n.id === id);
      if (!node) return;

      dragRef.current = {
        id,
        offsetX: (e.clientX - rect.left) - (node.x ?? 0),
        offsetY: (e.clientY - rect.top) - (node.y ?? 0),
      };

      const sim = simulationRef.current;
      if (sim) {
        node.fx = node.x;
        node.fy = node.y;
        sim.alphaTarget(0.3).restart();
      }
    },
    [simNodes],
  );

  useEffect(() => {
    function onMouseMove(e: MouseEvent) {
      if (!dragRef.current || !svgRef.current) return;
      const rect = svgRef.current.getBoundingClientRect();
      const node = simNodes.find((n) => n.id === dragRef.current!.id);
      if (!node) return;
      node.fx = e.clientX - rect.left - dragRef.current.offsetX;
      node.fy = e.clientY - rect.top - dragRef.current.offsetY;
    }

    function onMouseUp() {
      if (!dragRef.current) return;
      const node = simNodes.find((n) => n.id === dragRef.current!.id);
      if (node) {
        node.fx = null;
        node.fy = null;
      }
      dragRef.current = null;
      const sim = simulationRef.current;
      if (sim) sim.alphaTarget(0);
    }

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [simNodes]);

  const handleSelectNode = useCallback((id: string) => {
    setSelectedEdge(null);
    setSelectedNodeId((prev) => (prev === id ? null : id));
  }, []);

  const handleSelectEdge = useCallback((edge: EdgeData) => {
    setSelectedNodeId(null);
    setSelectedEdge((prev) =>
      prev && prev.source === edge.source && prev.target === edge.target ? null : edge,
    );
  }, []);

  const selectedNode = selectedNodeId ? nodes.find((n) => n.id === selectedNodeId) ?? null : null;

  if (!nodes.length) {
    return (
      <div className="text-sm text-text-muted text-center py-8">
        No agent interaction data available.
      </div>
    );
  }

  return (
    <div className="flex gap-4">
      <div className="flex-1 border border-border rounded-lg overflow-hidden bg-bg-subtle">
        <svg ref={svgRef} width={WIDTH} height={HEIGHT} className="w-full h-auto">
          {/* Edges */}
          <g>
            {simLinks.map((link, i) => {
              const src = link.source as SimNode;
              const tgt = link.target as SimNode;
              if (src.x == null || tgt.x == null) return null;

              const isHighlighted =
                selectedNodeId === null ||
                src.id === selectedNodeId ||
                tgt.id === selectedNodeId;

              const edgeData = edges.find(
                (e) => e.source === src.id && e.target === tgt.id,
              );

              return (
                <line
                  key={i}
                  x1={src.x}
                  y1={src.y}
                  x2={tgt.x}
                  y2={tgt.y}
                  stroke="currentColor"
                  strokeWidth={edgeWidthScale(link.weight)}
                  strokeOpacity={isHighlighted ? 0.4 : 0.08}
                  className="text-text-muted transition-opacity duration-200 cursor-pointer"
                  onClick={() => edgeData && handleSelectEdge(edgeData)}
                />
              );
            })}
          </g>

          {/* Nodes */}
          <g>
            {simNodes.map((node) => (
              <NetworkNode
                key={node.id}
                id={node.id}
                x={node.x ?? 0}
                y={node.y ?? 0}
                tokens={node.tokens}
                errorRate={node.error_rate}
                runs={node.runs}
                maxTokens={maxTokens}
                selected={selectedNodeId === node.id}
                onSelect={handleSelectNode}
                onDragStart={handleDragStart}
              />
            ))}
          </g>
        </svg>
      </div>

      {/* Detail panel */}
      <div className="w-[260px] shrink-0">
        <NetworkDetailPanel
          selectedNode={selectedNode}
          selectedEdge={selectedEdge}
          onClose={() => {
            setSelectedNodeId(null);
            setSelectedEdge(null);
          }}
        />
      </div>
    </div>
  );
}
