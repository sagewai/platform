'use client';

import { scaleLinear } from 'd3-scale';

interface NetworkNodeProps {
  id: string;
  x: number;
  y: number;
  tokens: number;
  errorRate: number;
  runs: number;
  maxTokens: number;
  selected: boolean;
  onSelect: (id: string) => void;
  onDragStart: (id: string, e: React.MouseEvent) => void;
}

// Size scaled by tokens
const sizeScale = scaleLinear().domain([0, 1]).range([20, 50]).clamp(true);

// Color by error rate: green (0%) -> yellow (5%) -> red (>10%)
const colorScale = scaleLinear<string>()
  .domain([0, 0.05, 0.1])
  .range(['#22c55e', '#eab308', '#ef4444'])
  .clamp(true);

export function NetworkNode({
  id,
  x,
  y,
  tokens,
  errorRate,
  runs,
  maxTokens,
  selected,
  onSelect,
  onDragStart,
}: NetworkNodeProps) {
  const normalized = maxTokens > 0 ? tokens / maxTokens : 0.5;
  const radius = sizeScale(normalized);
  const color = colorScale(errorRate);

  return (
    <g
      transform={`translate(${x}, ${y})`}
      onClick={() => onSelect(id)}
      onMouseDown={(e) => onDragStart(id, e)}
      className="cursor-grab active:cursor-grabbing"
    >
      <circle
        r={radius}
        fill={color}
        fillOpacity={0.2}
        stroke={color}
        strokeWidth={selected ? 3 : 1.5}
        className="transition-all duration-200"
      />
      {selected && (
        <circle r={radius + 4} fill="none" stroke={color} strokeWidth={1} strokeDasharray="4 2" />
      )}
      <text
        y={radius + 14}
        textAnchor="middle"
        fill="currentColor"
        className="text-[11px] text-text-primary pointer-events-none select-none"
      >
        {id}
      </text>
      <text
        y={-radius - 6}
        textAnchor="middle"
        fill="currentColor"
        className="text-[9px] text-text-muted pointer-events-none select-none"
      >
        {runs} runs
      </text>
    </g>
  );
}
