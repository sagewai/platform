'use client';

import { useEffect, useRef, useState } from 'react';
import {
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from 'framer-motion';
import type { Edge } from '@xyflow/react';
import { useMissionEventHistory } from '@/lib/mission-events/provider';
import { useDocumentVisible } from '@/lib/mission-events/visibility';
import type { MissionRunEvent } from '@/utils/types';

const PARTICLES_PER_HANDOFF = 4;
const DURATION_MS = 900;
const MAX_CONCURRENT_BURSTS = 7; // 7 × 4 = 28, under the 30 hard cap

interface ActiveBurst {
  id: string;
  edgeId: string;
}

// Walks the event history and emits a synthetic handoff whenever an
// agent.finished is followed by an agent.started for a different node, AND
// the (from, to) pair is a known edge. Returns the index up to which we have
// already processed, so subsequent calls only consider new tail events.
function findHandoffs(
  history: readonly MissionRunEvent[],
  edges: Edge[],
  alreadySeen: number,
): { newPairs: { from: string; to: string }[]; nextIndex: number } {
  const edgeSet = new Set(edges.map((e) => `${e.source}->${e.target}`));
  const newPairs: { from: string; to: string }[] = [];

  let lastFinished: string | null = null;
  // Re-walk from start to track lastFinished correctly, but only emit pairs
  // whose `started` event index is >= alreadySeen.
  for (let i = 0; i < history.length; i++) {
    const e = history[i];
    const owner = e.node_id ?? e.agent_id;
    if (e.kind === 'agent.finished' && owner) {
      lastFinished = owner;
    }
    if (e.kind === 'agent.started' && owner && lastFinished && owner !== lastFinished) {
      const key = `${lastFinished}->${owner}`;
      if (edgeSet.has(key) && i >= alreadySeen) {
        newPairs.push({ from: lastFinished, to: owner });
      }
      lastFinished = null;
    }
  }
  return { newPairs, nextIndex: history.length };
}

export function DataFlowParticles({ edges }: { edges: Edge[] }) {
  const reduced = useReducedMotion();
  const visible = useDocumentVisible();
  const history = useMissionEventHistory();
  const [bursts, setBursts] = useState<ActiveBurst[]>([]);
  const seqRef = useRef(0);
  const seenIndexRef = useRef(0);

  useEffect(() => {
    if (reduced || !visible) {
      seenIndexRef.current = history.length;
      return;
    }
    const { newPairs, nextIndex } = findHandoffs(history, edges, seenIndexRef.current);
    if (newPairs.length === 0) {
      seenIndexRef.current = nextIndex;
      return;
    }

    const additions: ActiveBurst[] = newPairs.flatMap((p) => {
      const edge = edges.find((e) => e.source === p.from && e.target === p.to);
      if (!edge) return [];
      const id = `b${seqRef.current++}`;
      return [{ id, edgeId: edge.id }];
    });

    setBursts((prev) => {
      const merged = [...prev, ...additions];
      while (merged.length > MAX_CONCURRENT_BURSTS) merged.shift();
      return merged;
    });

    seenIndexRef.current = nextIndex;

    // Schedule cleanup of these bursts.
    const handles = additions.map((b) =>
      window.setTimeout(() => {
        setBursts((prev) => prev.filter((x) => x.id !== b.id));
      }, DURATION_MS),
    );
    return () => handles.forEach((h) => window.clearTimeout(h));
  }, [history, edges, reduced, visible]);

  if (reduced) return null;
  if (!visible) return null;

  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      data-testid="data-flow-particles-overlay"
    >
      {bursts.map((b) => (
        <Burst key={b.id} edgeId={b.edgeId} />
      ))}
    </svg>
  );
}

function Burst({ edgeId }: { edgeId: string }) {
  const [path, setPath] = useState<SVGPathElement | null>(null);
  useEffect(() => {
    setPath(document.querySelector<SVGPathElement>(`path[data-id="${edgeId}"]`));
  }, [edgeId]);
  if (!path) return null;
  let total = 100;
  try {
    total = path.getTotalLength() || 100;
  } catch {
    total = 100;
  }
  const stagger = (DURATION_MS / 1000) * 0.08;
  return (
    <>
      {Array.from({ length: PARTICLES_PER_HANDOFF }).map((_, i) => (
        <Particle key={i} path={path} total={total} delay={i * stagger} />
      ))}
    </>
  );
}

function Particle({
  path,
  total,
  delay,
}: {
  path: SVGPathElement;
  total: number;
  delay: number;
}) {
  const pos = useMotionValue(0);
  const cx = useTransform(pos, (v) => {
    try {
      return path.getPointAtLength(v * total).x;
    } catch {
      return 0;
    }
  });
  const cy = useTransform(pos, (v) => {
    try {
      return path.getPointAtLength(v * total).y;
    } catch {
      return 0;
    }
  });

  useEffect(() => {
    const ctrl = animate(pos, 1, {
      duration: DURATION_MS / 1000,
      ease: 'easeInOut',
      delay,
    });
    return () => ctrl.stop();
  }, [pos, delay]);

  return (
    <motion.circle
      r={3}
      cx={cx}
      cy={cy}
      className="fill-primary"
      data-testid="flow-particle"
      initial={{ opacity: 0 }}
      animate={{ opacity: [0, 1, 1, 0] }}
      transition={{
        duration: DURATION_MS / 1000,
        delay,
        times: [0, 0.15, 0.85, 1],
      }}
    />
  );
}
