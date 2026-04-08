'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import { sankey, sankeyLinkHorizontal, type SankeyNode, type SankeyLink } from 'd3-sankey';
import { scaleLinear } from 'd3-scale';
import { format } from 'd3-format';
import { SankeyTooltip } from './sankey-tooltip';

/** Per-agent token breakdown from a workflow run */
interface AgentTokenData {
  name: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

interface SankeyDiagramProps {
  agents: AgentTokenData[];
  /** Approximate cost-per-token map by model prefix (defaults provided) */
  costRates?: Record<string, number>;
}

const DEFAULT_RATES: Record<string, number> = {
  'gpt-4o': 0.000005,
  'gpt-4': 0.00003,
  'gpt-3.5': 0.0000005,
  'claude': 0.000008,
  'gemini': 0.0000025,
  default: 0.000005,
};

function getRate(model: string, rates: Record<string, number>): number {
  const lower = model.toLowerCase();
  for (const [prefix, rate] of Object.entries(rates)) {
    if (prefix !== 'default' && lower.includes(prefix)) return rate;
  }
  return rates.default ?? 0.000005;
}

// Color gradient: green (<$0.01) -> yellow -> orange -> red (>$1.00)
const costColorScale = scaleLinear<string>()
  .domain([0, 0.01, 0.1, 1.0])
  .range(['#22c55e', '#eab308', '#f97316', '#ef4444'])
  .clamp(true);

interface TooltipState {
  visible: boolean;
  x: number;
  y: number;
  label: string;
  tokens: number;
  cost: number;
}

type SNode = SankeyNode<{ id: string; label: string; category: string }, { tokens: number; cost: number }>;
type SLink = SankeyLink<{ id: string; label: string; category: string }, { tokens: number; cost: number }>;

const fmtTokens = format(',');

export function SankeyDiagram({ agents, costRates }: SankeyDiagramProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const rates = costRates ?? DEFAULT_RATES;
  const [tooltip, setTooltip] = useState<TooltipState>({
    visible: false,
    x: 0,
    y: 0,
    label: '',
    tokens: 0,
    cost: 0,
  });
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const WIDTH = 700;
  const HEIGHT = Math.max(300, agents.length * 80);

  const { nodes, links } = useMemo(() => {
    if (!agents.length) return { nodes: [] as SNode[], links: [] as SLink[] };

    // Build node list: Input Sources (left) -> Agents (middle) -> Outputs (right)
    const nodeMap = new Map<string, number>();
    const rawNodes: Array<{ id: string; label: string; category: string }> = [];
    const rawLinks: Array<{ source: number; target: number; tokens: number; cost: number }> = [];

    function getOrCreateNode(id: string, label: string, category: string) {
      if (!nodeMap.has(id)) {
        nodeMap.set(id, rawNodes.length);
        rawNodes.push({ id, label, category });
      }
      return nodeMap.get(id)!;
    }

    for (const agent of agents) {
      const rate = getRate(agent.model, rates);
      const inputCost = agent.input_tokens * rate;
      const outputCost = agent.output_tokens * rate;

      // Input source node
      const srcId = `input:${agent.model}`;
      const srcIdx = getOrCreateNode(srcId, agent.model, 'input');

      // Agent node (middle)
      const agentIdx = getOrCreateNode(`agent:${agent.name}`, agent.name, 'agent');

      // Output node
      const outIdx = getOrCreateNode(`output:${agent.name}`, `${agent.name} output`, 'output');

      // Link: input source -> agent
      rawLinks.push({
        source: srcIdx,
        target: agentIdx,
        tokens: agent.input_tokens,
        cost: inputCost,
      });

      // Link: agent -> output
      rawLinks.push({
        source: agentIdx,
        target: outIdx,
        tokens: agent.output_tokens,
        cost: outputCost,
      });
    }

    // Build sankey layout
    const sankeyLayout = sankey<
      { id: string; label: string; category: string },
      { tokens: number; cost: number }
    >()
      .nodeId((d) => d.id)
      .nodeWidth(16)
      .nodePadding(20)
      .extent([
        [10, 10],
        [WIDTH - 10, HEIGHT - 10],
      ]);

    // Create proper node/link structures with id-based references (nodeId uses d.id)
    const graphNodes = rawNodes.map((n) => ({ ...n }));
    const graphLinks = rawLinks.map((l) => ({
      source: rawNodes[l.source].id,
      target: rawNodes[l.target].id,
      value: Math.max(l.tokens, 1),
      tokens: l.tokens,
      cost: l.cost,
    }));

    const result = sankeyLayout({
      nodes: graphNodes,
      links: graphLinks,
    });

    return { nodes: result.nodes as SNode[], links: result.links as SLink[] };
  }, [agents, rates, WIDTH, HEIGHT]);

  const handleLinkHover = useCallback(
    (e: React.MouseEvent, link: SLink) => {
      const src = link.source as SNode;
      const tgt = link.target as SNode;
      setTooltip({
        visible: true,
        x: e.clientX,
        y: e.clientY,
        label: `${src.label} \u2192 ${tgt.label}`,
        tokens: link.tokens,
        cost: link.cost,
      });
    },
    [],
  );

  const handleNodeClick = useCallback((node: SNode) => {
    setSelectedNode((prev) => (prev === node.id ? null : node.id));
  }, []);

  if (!agents.length) {
    return (
      <div className="text-sm text-text-muted text-center py-8">
        No token data available for Sankey visualization.
      </div>
    );
  }

  const pathGenerator = sankeyLinkHorizontal();

  return (
    <div className="relative">
      <svg ref={svgRef} width={WIDTH} height={HEIGHT} className="w-full h-auto">
        {/* Links */}
        <g>
          {links.map((link, i) => {
            const d = pathGenerator(link as any);
            if (!d) return null;
            const color = costColorScale(link.cost);
            const isHighlighted =
              selectedNode === null ||
              (link.source as SNode).id === selectedNode ||
              (link.target as SNode).id === selectedNode;

            return (
              <path
                key={i}
                d={d}
                fill="none"
                stroke={color}
                strokeWidth={Math.max((link as any).width ?? 1, 2)}
                strokeOpacity={isHighlighted ? 0.5 : 0.1}
                className="transition-opacity duration-200 cursor-pointer"
                onMouseMove={(e) => handleLinkHover(e, link)}
                onMouseLeave={() => setTooltip((t) => ({ ...t, visible: false }))}
              />
            );
          })}
        </g>

        {/* Nodes */}
        <g>
          {nodes.map((node) => {
            const x0 = node.x0 ?? 0;
            const y0 = node.y0 ?? 0;
            const x1 = node.x1 ?? 0;
            const y1 = node.y1 ?? 0;
            const isHighlighted = selectedNode === null || selectedNode === node.id;
            const fillColor =
              node.category === 'input'
                ? '#3b82f6'
                : node.category === 'agent'
                  ? '#8b5cf6'
                  : '#22c55e';

            return (
              <g key={node.id} onClick={() => handleNodeClick(node)} className="cursor-pointer">
                <rect
                  x={x0}
                  y={y0}
                  width={x1 - x0}
                  height={Math.max(y1 - y0, 4)}
                  fill={fillColor}
                  fillOpacity={isHighlighted ? 0.9 : 0.3}
                  rx={2}
                  className="transition-opacity duration-200"
                />
                <text
                  x={node.category === 'output' ? x0 - 6 : x1 + 6}
                  y={(y0 + y1) / 2}
                  dy="0.35em"
                  textAnchor={node.category === 'output' ? 'end' : 'start'}
                  fill="currentColor"
                  className="text-[11px] text-text-primary"
                >
                  {node.label}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <SankeyTooltip {...tooltip} />

      {/* Node detail panel */}
      {selectedNode && (() => {
        const node = nodes.find((n) => n.id === selectedNode);
        if (!node) return null;
        const agentName = node.id.replace(/^(input|agent|output):/, '');
        const agent = agents.find((a) => a.name === agentName || a.model === agentName);
        if (!agent) return null;
        const rate = getRate(agent.model, rates);
        const totalCost = agent.total_tokens * rate;

        return (
          <div className="mt-3 p-3 border border-border rounded-lg bg-bg-subtle text-xs">
            <div className="font-semibold text-text-primary mb-2">{agent.name}</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-text-muted">
              <span>Model</span>
              <span className="font-[family-name:var(--font-mono)]">{agent.model}</span>
              <span>Input tokens</span>
              <span>{fmtTokens(agent.input_tokens)}</span>
              <span>Output tokens</span>
              <span>{fmtTokens(agent.output_tokens)}</span>
              <span>Total tokens</span>
              <span className="font-semibold text-text-primary">{fmtTokens(agent.total_tokens)}</span>
              <span>Est. cost</span>
              <span className="font-semibold text-text-primary">${totalCost.toFixed(4)}</span>
            </div>
          </div>
        );
      })()}

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 text-[10px] text-text-muted">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm inline-block" style={{ background: '#22c55e' }} />
          &lt;$0.01
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm inline-block" style={{ background: '#eab308' }} />
          ~$0.01
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm inline-block" style={{ background: '#f97316' }} />
          ~$0.10
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm inline-block" style={{ background: '#ef4444' }} />
          &gt;$1.00
        </span>
      </div>
    </div>
  );
}
