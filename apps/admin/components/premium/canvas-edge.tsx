'use client';

import { memo } from 'react';
import { getBezierPath, type EdgeProps } from '@xyflow/react';

export interface CanvasEdgeData {
  animated?: boolean;
  status?: 'idle' | 'active' | 'completed';
  [key: string]: unknown;
}

const statusColors: Record<string, string> = {
  idle: '#4b5563',
  active: '#26c6da',
  completed: '#22c55e',
};

function CanvasEdgeComponent({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  style = {},
}: EdgeProps) {
  const edgeData = (data ?? {}) as CanvasEdgeData;
  const status = edgeData.status || 'idle';
  const isAnimated = edgeData.animated ?? status === 'active';
  const color = statusColors[status] || statusColors.idle;

  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  return (
    <>
      <path
        id={id}
        d={edgePath}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeDasharray={isAnimated ? '6 4' : undefined}
        style={{
          ...style,
          animation: isAnimated ? 'dash-flow 0.6s linear infinite' : undefined,
        }}
      />
      <style>{`
        @keyframes dash-flow {
          to { stroke-dashoffset: -10; }
        }
      `}</style>
    </>
  );
}

export const CanvasEdge = memo(CanvasEdgeComponent);
