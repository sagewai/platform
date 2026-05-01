'use client';

/**
 * HudGraph — D3 force-simulation graph of agents + infrastructure actors.
 *
 * React owns: the node/link data and the currently-selected/focused/filtered state.
 * D3 owns: the DOM nodes inside <svg>, the simulation tick loop, and the
 * particle animation RAF.
 *
 * We render the graph imperatively via refs so React doesn't rerender on every
 * simulation tick. State changes from React (selection, workflow highlight,
 * project filter, tweak params) flow in through an imperative handle.
 */

import { useEffect, useImperativeHandle, useRef, forwardRef } from 'react';
import * as d3 from 'd3';
import {
  actorColor,
  roleColor,
  type HudActor,
  type HudActorKind,
  type HudActorShape,
  type HudAgent,
  type HudLink,
  type HudWorkflow,
} from '../lib/hud-data';

export interface HudGraphHandle {
  setSelected: (id: string | null) => void;
  highlightWorkflow: (wf: HudWorkflow | null) => void;
  setProjectFilter: (projectId: string | null) => void;
  setParams: (params: { speed?: number; density?: number }) => void;
}

interface HudGraphProps {
  agents: HudAgent[];
  actors: HudActor[];
  links: HudLink[];
  onSelectAgent: (agent: HudAgent | HudActor | null) => void;
}

type GraphNode = (HudAgent | HudActor) & {
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
};

type SimLink = d3.SimulationLinkDatum<GraphNode> & {
  kind: string;
  volume: number;
  source: GraphNode | string;
  target: GraphNode | string;
};

interface Particle {
  link: SimLink;
  t: number;
  speed: number;
  color: string;
  born: number;
}

function hexPoints(r: number): string {
  const pts: string[] = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i - Math.PI / 6;
    pts.push(`${(Math.cos(a) * r).toFixed(2)},${(Math.sin(a) * r).toFixed(2)}`);
  }
  return pts.join(' ');
}

function drawActorShape(
  sel: d3.Selection<SVGGElement, GraphNode, null, undefined>,
  shape: HudActorShape,
  color: string,
) {
  const fill = 'rgba(5,12,18,0.92)';
  if (shape === 'ring') {
    sel.append('circle').attr('r', 22).attr('fill', fill).attr('stroke', color).attr('stroke-width', 1.4);
    sel
      .append('circle')
      .attr('r', 15)
      .attr('fill', 'none')
      .attr('stroke', color)
      .attr('stroke-opacity', 0.5)
      .attr('stroke-width', 1);
    sel
      .append('circle')
      .attr('r', 8)
      .attr('fill', 'none')
      .attr('stroke', color)
      .attr('stroke-opacity', 0.7)
      .attr('stroke-width', 1);
    for (const a of [0, 90, 180, 270]) {
      const rad = (a * Math.PI) / 180;
      sel
        .append('line')
        .attr('x1', Math.cos(rad) * 20)
        .attr('y1', Math.sin(rad) * 20)
        .attr('x2', Math.cos(rad) * 26)
        .attr('y2', Math.sin(rad) * 26)
        .attr('stroke', color)
        .attr('stroke-opacity', 0.9)
        .attr('stroke-width', 1);
    }
  } else if (shape === 'slab') {
    sel
      .append('rect')
      .attr('x', -24).attr('y', -14).attr('width', 48).attr('height', 28)
      .attr('fill', fill).attr('stroke', color).attr('stroke-width', 1.3);
    sel
      .append('line')
      .attr('x1', -24).attr('y1', -9).attr('x2', 24).attr('y2', -9)
      .attr('stroke', color).attr('stroke-opacity', 0.5);
    for (let i = 0; i < 4; i++) {
      sel
        .append('rect')
        .attr('x', -20 + i * 5).attr('y', -13).attr('width', 2).attr('height', 2)
        .attr('fill', color).attr('opacity', 0.6 + ((i * 7) % 10) / 25);
    }
    sel
      .append('line')
      .attr('x1', -28).attr('y1', 0).attr('x2', -22).attr('y2', 0)
      .attr('stroke', color).attr('stroke-opacity', 0.7);
    sel
      .append('line')
      .attr('x1', 22).attr('y1', 0).attr('x2', 28).attr('y2', 0)
      .attr('stroke', color).attr('stroke-opacity', 0.7);
  } else if (shape === 'diamond') {
    sel
      .append('polygon')
      .attr('points', '0,-22 22,0 0,22 -22,0')
      .attr('fill', fill).attr('stroke', color).attr('stroke-width', 1.4);
    sel
      .append('polygon')
      .attr('points', '0,-12 12,0 0,12 -12,0')
      .attr('fill', 'none').attr('stroke', color).attr('stroke-opacity', 0.5);
    sel.append('circle').attr('r', 3).attr('fill', color);
  } else if (shape === 'shield') {
    sel
      .append('path')
      .attr('d', 'M 0 -24 L 20 -16 L 20 6 Q 20 20 0 24 Q -20 20 -20 6 L -20 -16 Z')
      .attr('fill', fill).attr('stroke', color).attr('stroke-width', 1.4);
    sel
      .append('path')
      .attr('d', 'M 0 -14 L 11 -9 L 11 4 Q 11 12 0 16 Q -11 12 -11 4 L -11 -9 Z')
      .attr('fill', 'none').attr('stroke', color).attr('stroke-opacity', 0.5);
    sel.append('circle').attr('r', 2.5).attr('cy', 0).attr('fill', color);
  }
}

function pairInPath(aId: string, bId: string, path: string[]): boolean {
  for (let i = 0; i < path.length - 1; i++) {
    if ((path[i] === aId && path[i + 1] === bId) || (path[i] === bId && path[i + 1] === aId)) {
      return true;
    }
  }
  return false;
}

function pointOnLink(l: SimLink, t: number): { x: number; y: number } {
  const s = l.source as GraphNode;
  const tgt = l.target as GraphNode;
  const sx = s.x ?? 0;
  const sy = s.y ?? 0;
  const tx = tgt.x ?? 0;
  const ty = tgt.y ?? 0;
  const dx = tx - sx;
  const dy = ty - sy;
  const mx = (sx + tx) / 2;
  const my = (sy + ty) / 2;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  const k = 18;
  const cx = mx + nx * k;
  const cy = my + ny * k;
  const u = 1 - t;
  return {
    x: u * u * sx + 2 * u * t * cx + t * t * tx,
    y: u * u * sy + 2 * u * t * cy + t * t * ty,
  };
}

export const HudGraph = forwardRef<HudGraphHandle, HudGraphProps>(function HudGraph(
  { agents, actors, links, onSelectAgent },
  ref,
) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simRef = useRef<d3.Simulation<GraphNode, SimLink> | null>(null);
  const rootGRef = useRef<d3.Selection<SVGGElement, unknown, null, undefined> | null>(null);
  const nodeSelRef = useRef<d3.Selection<SVGGElement, GraphNode, SVGGElement, unknown> | null>(null);
  const linkSelRef = useRef<d3.Selection<SVGPathElement, SimLink, SVGGElement, unknown> | null>(null);
  const haloSelRef = useRef<d3.Selection<SVGCircleElement, GraphNode, SVGGElement, unknown> | null>(null);
  const particleLayerRef = useRef<d3.Selection<SVGGElement, unknown, null, undefined> | null>(null);
  const particlesRef = useRef<Particle[]>([]);
  const nodeByIdRef = useRef<Map<string, GraphNode>>(new Map());
  const selectedIdRef = useRef<string | null>(null);
  const highlightedPathRef = useRef<string[] | null>(null);
  const currentProjectRef = useRef<string | null>(null);
  const paramsRef = useRef<{ speed: number; density: number }>({ speed: 1, density: 1 });
  const focusStateRef = useRef<{ id: string; scale: number } | null>(null);
  const rafRef = useRef<number | null>(null);

  // Width/height of SVG (set once)
  const widthRef = useRef(0);
  const heightRef = useRef(0);

  useEffect(() => {
    const svgEl = svgRef.current;
    if (!svgEl) return;

    // Stage layout is hardcoded: #graph-area is 1080×790 inside the 1920×1080 stage.
    // Measuring via getBoundingClientRect() returns 0 before the scaler useEffect
    // transforms the parent — just use the known design-time dimensions.
    const width = 1080;
    const height = 790;
    widthRef.current = width;
    heightRef.current = height;

    const svg = d3.select(svgEl);
    svg.attr('viewBox', `0 0 ${width} ${height}`);
    svg.attr('preserveAspectRatio', 'xMidYMid meet');
    svg.selectAll('*').remove();

    // Clone nodes so the simulation can mutate x/y without touching React state
    const simAgents: GraphNode[] = agents.map((a) => ({ ...a }));
    const simActors: GraphNode[] = actors.map((a) => ({ ...a }));
    const allNodes: GraphNode[] = [...simAgents, ...simActors];
    const byId = new Map(allNodes.map((n) => [n.id, n]));
    nodeByIdRef.current = byId;

    const simLinks: SimLink[] = links.map((l) => ({
      source: l.source,
      target: l.target,
      kind: l.kind,
      volume: l.volume,
    }));

    const g = svg.append('g').attr('class', 'graph-root');
    rootGRef.current = g;
    const linkLayer = g.append('g').attr('class', 'link-layer');
    const particleLayer = g.append('g').attr('class', 'particle-layer');
    const nodeLayer = g.append('g').attr('class', 'node-layer');
    particleLayerRef.current = particleLayer;

    const cx = width / 2;
    const cy = height / 2;
    g.append('circle').attr('cx', cx).attr('cy', cy).attr('r', 3)
      .attr('fill', 'var(--cyan)').attr('opacity', 0.6);

    const simulation = d3
      .forceSimulation<GraphNode>(allNodes)
      .force(
        'link',
        d3
          .forceLink<GraphNode, SimLink>(simLinks)
          .id((d) => d.id)
          .distance(150)
          .strength(0.4),
      )
      .force('charge', d3.forceManyBody().strength(-520))
      .force('center', d3.forceCenter(cx, cy))
      .force('collide', d3.forceCollide(42))
      .alpha(0.9);
    simRef.current = simulation;

    // Warm-start: advance the simulation synchronously so nodes have real
    // positions before the first paint. Otherwise d3 only begins ticking on
    // the next animation frame, and the first paint shows the phyllotaxis
    // initialisation (all nodes clustered at origin).
    simulation.tick(180);

    const linkSel = linkLayer
      .selectAll<SVGPathElement, SimLink>('path')
      .data(simLinks)
      .join('path')
      .attr('class', 'link-base')
      .attr('stroke', 'var(--cyan)');
    linkSelRef.current = linkSel;

    const nodeG = nodeLayer
      .selectAll<SVGGElement, GraphNode>('g.node-g')
      .data(allNodes, (d) => d.id)
      .join('g')
      .attr('class', (d) => `node-g ${d.nodeClass}`)
      .on('click', function (event, d) {
        event.stopPropagation();
        selectedIdRef.current = d.id;
        nodeG.classed('selected', (n) => n.id === selectedIdRef.current);
        applyFocus(d);
        onSelectAgent(d);
      })
      .call(
        d3
          .drag<SVGGElement, GraphNode>()
          .on('start', (e, d) => {
            if (!e.active) simulation.alphaTarget(0.25).restart();
            d.fx = d.x ?? 0;
            d.fy = d.y ?? 0;
          })
          .on('drag', (e, d) => {
            d.fx = e.x;
            d.fy = e.y;
          })
          .on('end', (e, d) => {
            if (!e.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          }),
      );
    nodeSelRef.current = nodeG;

    const haloSel = nodeG
      .append('circle')
      .attr('class', 'node-halo')
      .attr('r', (d) => (d.nodeClass === 'actor' ? 34 : 26));
    haloSelRef.current = haloSel;

    // Agent visuals: hex core + side tick marks + outer ring
    const agentG = nodeG.filter((d) => d.nodeClass === 'agent');
    agentG
      .append('circle')
      .attr('class', 'node-ring')
      .attr('r', 20)
      .attr('stroke', (d) => roleColor((d as HudAgent).role))
      .attr('stroke-opacity', 0.45);
    agentG
      .append('polygon')
      .attr('class', 'node-core')
      .attr('points', hexPoints(13))
      .attr('fill', 'rgba(5,12,18,0.9)')
      .attr('stroke', (d) => roleColor((d as HudAgent).role));
    agentG
      .append('line')
      .attr('x1', -18).attr('x2', -9).attr('y1', 0).attr('y2', 0)
      .attr('stroke', (d) => roleColor((d as HudAgent).role))
      .attr('stroke-opacity', 0.6).attr('stroke-width', 1);
    agentG
      .append('line')
      .attr('x1', 9).attr('x2', 18).attr('y1', 0).attr('y2', 0)
      .attr('stroke', (d) => roleColor((d as HudAgent).role))
      .attr('stroke-opacity', 0.6).attr('stroke-width', 1);

    // Actor visuals: shape per kind
    const actorG = nodeG.filter((d) => d.nodeClass === 'actor');
    actorG.each(function (d) {
      const sel = d3.select<SVGGElement, GraphNode>(this);
      const actor = d as HudActor;
      drawActorShape(sel, actor.shape, actorColor(actor.kind as HudActorKind));
    });

    nodeG
      .append('text')
      .attr('class', 'node-label')
      .attr('y', (d) => (d.nodeClass === 'actor' ? 46 : 38))
      .text((d) => d.name);
    nodeG
      .append('text')
      .attr('class', 'node-sublabel')
      .attr('y', (d) => (d.nodeClass === 'actor' ? 59 : 51))
      .text((d) =>
        d.nodeClass === 'actor' ? (d as HudActor).kind.toUpperCase() : d.id,
      );

    const renderTick = () => {
      linkSel.attr('d', (d) => {
        const s = d.source as GraphNode;
        const t = d.target as GraphNode;
        const sx = s.x ?? 0;
        const sy = s.y ?? 0;
        const tx = t.x ?? 0;
        const ty = t.y ?? 0;
        const dx = tx - sx;
        const dy = ty - sy;
        const mx = (sx + tx) / 2;
        const my = (sy + ty) / 2;
        const len = Math.hypot(dx, dy) || 1;
        const nx = -dy / len;
        const ny = dx / len;
        const k = 18;
        const cpx = mx + nx * k;
        const cpy = my + ny * k;
        return `M${sx},${sy} Q${cpx},${cpy} ${tx},${ty}`;
      });
      nodeG.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);

      // If a node is focused, keep viewport snapped to it when simulation settles
      const fs = focusStateRef.current;
      if (fs) {
        const node = nodeByIdRef.current.get(fs.id);
        if (node && node.x != null && node.y != null) {
          const s = fs.scale;
          const tx = widthRef.current / 2 - node.x * s;
          const ty = heightRef.current / 2 - node.y * s;
          if (simulation.alpha() < 0.08) {
            g.attr('transform', `translate(${tx},${ty}) scale(${s})`);
          }
        }
      }

      // Halo pulse by status
      haloSel.each(function (d) {
        const el = d3.select(this);
        const now = performance.now() / 1000;
        const baseR = d.nodeClass === 'actor' ? 34 : 26;
        let r = baseR;
        let o = 0.18;
        let col =
          d.nodeClass === 'actor'
            ? actorColor((d as HudActor).kind as HudActorKind)
            : roleColor((d as HudAgent).role);
        if (d.status === 'thinking') {
          r = baseR + Math.sin(now * 3 + (d.x ?? 0) * 0.01) * 6;
          o = 0.35;
        } else if (d.status === 'alert') {
          r = baseR + 2 + Math.sin(now * 8) * 5;
          o = 0.55;
          col = 'var(--red)';
        } else if (d.status === 'idle') {
          o = 0.08;
        }
        el.attr('r', r).attr('stroke', col).attr('stroke-opacity', o);
      });
    };
    simulation.on('tick', renderTick);
    // Paint the warm-started state now so the graph is visible on first frame.
    renderTick();

    svg.on('click', () => {
      selectedIdRef.current = null;
      nodeG.classed('selected', false);
      applyFocus(null);
      onSelectAgent(null);
    });

    // Particle animation
    const spawnParticle = () => {
      if (!simLinks.length) return;
      const path = highlightedPathRef.current;
      const eligible = path
        ? simLinks.filter((l) => {
            const sId = (l.source as GraphNode).id ?? (l.source as string);
            const tId = (l.target as GraphNode).id ?? (l.target as string);
            return pairInPath(sId, tId, path);
          })
        : simLinks;
      if (!eligible.length) return;
      const l = eligible[Math.floor(Math.random() * eligible.length)];
      particlesRef.current.push({
        link: l,
        t: 0,
        speed: 0.004 * paramsRef.current.speed * (0.7 + Math.random() * 0.8),
        color: path ? 'var(--amber)' : 'var(--cyan)',
        born: performance.now(),
      });
    };

    const particleFrame = () => {
      const n = Math.round(2 * paramsRef.current.density);
      for (let i = 0; i < n; i++) {
        if (Math.random() < 0.7) spawnParticle();
      }
      particlesRef.current.forEach((p) => (p.t += p.speed));

      const sel = particleLayer
        .selectAll<SVGCircleElement, Particle>('circle.particle')
        .data(particlesRef.current, (d) => `${d.born}-${particlesRef.current.indexOf(d)}`);
      sel
        .enter()
        .append('circle')
        .attr('class', 'particle')
        .attr('r', 2.2)
        .merge(sel as d3.Selection<SVGCircleElement, Particle, SVGGElement, unknown>)
        .attr('fill', (d) => d.color)
        .attr('opacity', (d) =>
          d.t < 0.1 ? d.t * 10 : d.t > 0.9 ? (1 - d.t) * 10 : 1,
        )
        .attr('cx', (d) => pointOnLink(d.link, d.t).x)
        .attr('cy', (d) => pointOnLink(d.link, d.t).y)
        .style('filter', (d) =>
          `drop-shadow(0 0 4px ${d.color === 'var(--amber)' ? 'rgba(255,179,71,0.9)' : 'rgba(107,231,255,0.9)'})`,
        );
      sel.exit().remove();

      for (let i = particlesRef.current.length - 1; i >= 0; i--) {
        if (particlesRef.current[i].t >= 1) particlesRef.current.splice(i, 1);
      }
      rafRef.current = requestAnimationFrame(particleFrame);
    };
    rafRef.current = requestAnimationFrame(particleFrame);

    // Let force layout cool off, then keep it barely ticking for the living-topology feel
    const settleTimer = window.setTimeout(() => simulation.alphaTarget(0.04).restart(), 1800);

    function applyFocus(node: GraphNode | null) {
      if (!node) {
        focusStateRef.current = null;
        g.transition().duration(650).ease(d3.easeCubicInOut).attr('transform', null);
        nodeG.classed('focus-dim', false).classed('focus-peer', false).classed('focus-root', false);
        linkSel.classed('focus-dim', false).classed('focus-peer', false);
        return;
      }
      focusStateRef.current = { id: node.id, scale: 1.55 };
      const peers = new Set<string>([node.id]);
      simLinks.forEach((l) => {
        const sId = (l.source as GraphNode).id ?? (l.source as string);
        const tId = (l.target as GraphNode).id ?? (l.target as string);
        if (sId === node.id) peers.add(tId);
        if (tId === node.id) peers.add(sId);
      });
      nodeG
        .classed('focus-root', (d) => d.id === node.id)
        .classed('focus-peer', (d) => peers.has(d.id) && d.id !== node.id)
        .classed('focus-dim', (d) => !peers.has(d.id));
      linkSel
        .classed('focus-peer', (d) => {
          const sId = (d.source as GraphNode).id ?? (d.source as string);
          const tId = (d.target as GraphNode).id ?? (d.target as string);
          return sId === node.id || tId === node.id;
        })
        .classed('focus-dim', (d) => {
          const sId = (d.source as GraphNode).id ?? (d.source as string);
          const tId = (d.target as GraphNode).id ?? (d.target as string);
          return sId !== node.id && tId !== node.id;
        });
      panToFocus();
    }

    function panToFocus() {
      const fs = focusStateRef.current;
      if (!fs) return;
      const node = nodeByIdRef.current.get(fs.id);
      if (!node || node.x == null || node.y == null) return;
      const s = fs.scale;
      const tx = widthRef.current / 2 - node.x * s;
      const ty = heightRef.current / 2 - node.y * s;
      g.transition()
        .duration(650)
        .ease(d3.easeCubicInOut)
        .attr('transform', `translate(${tx},${ty}) scale(${s})`);
    }

    return () => {
      simulation.stop();
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      window.clearTimeout(settleTimer);
      particlesRef.current = [];
      svg.selectAll('*').remove();
      svg.on('click', null);
    };
  }, [agents, actors, links, onSelectAgent]);

  useImperativeHandle(
    ref,
    () => ({
      setSelected(id) {
        selectedIdRef.current = id;
        const nodeG = nodeSelRef.current;
        if (!nodeG) return;
        nodeG.classed('selected', (n) => n.id === id);
        const node = id ? nodeByIdRef.current.get(id) : null;
        applyFocusFromHandle(node ?? null);
      },
      highlightWorkflow(wf) {
        highlightedPathRef.current = wf ? wf.path : null;
        const linkSel = linkSelRef.current;
        const nodeG = nodeSelRef.current;
        if (!linkSel || !nodeG) return;
        linkSel
          .classed('link-wf', (d) => {
            if (!wf) return false;
            const sId = (d.source as GraphNode).id ?? (d.source as string);
            const tId = (d.target as GraphNode).id ?? (d.target as string);
            return pairInPath(sId, tId, wf.path);
          })
          .classed('link-active', () => !wf);
        nodeG.style('opacity', (d) => {
          if (!wf) return 1;
          return wf.path.includes(d.id) ? 1 : 0.28;
        });
      },
      setProjectFilter(projectId) {
        currentProjectRef.current = projectId;
        const nodeG = nodeSelRef.current;
        const linkSel = linkSelRef.current;
        if (!nodeG || !linkSel) return;
        nodeG.classed('out-of-scope', (d) => {
          if (!projectId) return false;
          return !(d.projects ?? []).includes(projectId);
        });
        linkSel.classed('out-of-scope', (d) => {
          if (!projectId) return false;
          const sNode = d.source as GraphNode;
          const tNode = d.target as GraphNode;
          const sIn = (sNode.projects ?? []).includes(projectId);
          const tIn = (tNode.projects ?? []).includes(projectId);
          return !(sIn && tIn);
        });
      },
      setParams(p) {
        if (p.speed != null) paramsRef.current.speed = p.speed;
        if (p.density != null) paramsRef.current.density = p.density;
      },
    }),
    [],
  );

  // Bridge between the imperative handle and the inner applyFocus closure
  function applyFocusFromHandle(node: GraphNode | null) {
    const g = rootGRef.current;
    const nodeG = nodeSelRef.current;
    const linkSel = linkSelRef.current;
    if (!g || !nodeG || !linkSel) return;
    if (!node) {
      focusStateRef.current = null;
      g.transition().duration(650).ease(d3.easeCubicInOut).attr('transform', null);
      nodeG.classed('focus-dim', false).classed('focus-peer', false).classed('focus-root', false);
      linkSel.classed('focus-dim', false).classed('focus-peer', false);
      return;
    }
    focusStateRef.current = { id: node.id, scale: 1.55 };
    const peers = new Set<string>([node.id]);
    linkSel.each((d) => {
      const sId = (d.source as GraphNode).id ?? (d.source as string);
      const tId = (d.target as GraphNode).id ?? (d.target as string);
      if (sId === node.id) peers.add(tId);
      if (tId === node.id) peers.add(sId);
    });
    nodeG
      .classed('focus-root', (d) => d.id === node.id)
      .classed('focus-peer', (d) => peers.has(d.id) && d.id !== node.id)
      .classed('focus-dim', (d) => !peers.has(d.id));
    linkSel
      .classed('focus-peer', (d) => {
        const sId = (d.source as GraphNode).id ?? (d.source as string);
        const tId = (d.target as GraphNode).id ?? (d.target as string);
        return sId === node.id || tId === node.id;
      })
      .classed('focus-dim', (d) => {
        const sId = (d.source as GraphNode).id ?? (d.source as string);
        const tId = (d.target as GraphNode).id ?? (d.target as string);
        return sId !== node.id && tId !== node.id;
      });
    if (node.x != null && node.y != null) {
      const s = focusStateRef.current.scale;
      const tx = widthRef.current / 2 - node.x * s;
      const ty = heightRef.current / 2 - node.y * s;
      g.transition()
        .duration(650)
        .ease(d3.easeCubicInOut)
        .attr('transform', `translate(${tx},${ty}) scale(${s})`);
    }
  }

  return <svg ref={svgRef} style={{ width: '100%', height: '100%', display: 'block' }} />;
});
