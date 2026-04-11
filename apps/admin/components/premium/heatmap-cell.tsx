'use client';

import { useState } from 'react';
import { scaleLinear } from 'd3-scale';

interface HeatmapCellProps {
  x: number;
  y: number;
  width: number;
  height: number;
  workflowName: string;
  date: string;
  totalRuns: number;
  passed: number;
  failed: number;
  maxRuns: number;
  onClick: () => void;
}

// Color by success rate: green (100%) -> yellow (75%) -> orange (50%) -> red (<50%)
const successColorScale = scaleLinear<string>()
  .domain([0.0, 0.5, 0.75, 1.0])
  .range(['#ef4444', '#f97316', '#eab308', '#22c55e'])
  .clamp(true);

// Opacity scaled by run count
const opacityScale = scaleLinear().domain([0, 1]).range([0.2, 1.0]).clamp(true);

export function HeatmapCell({
  x,
  y,
  width,
  height,
  workflowName,
  date,
  totalRuns,
  passed,
  failed,
  maxRuns,
  onClick,
}: HeatmapCellProps) {
  const [hovered, setHovered] = useState(false);
  const successRate = totalRuns > 0 ? passed / totalRuns : 1;
  const color = successColorScale(successRate);
  const opacity = opacityScale(maxRuns > 0 ? totalRuns / maxRuns : 0);

  const formattedDate = new Date(date + 'T00:00:00').toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });

  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        fill={color}
        fillOpacity={opacity}
        rx={2}
        stroke={hovered ? 'currentColor' : 'transparent'}
        strokeWidth={hovered ? 1.5 : 0}
        className="cursor-pointer text-text-primary transition-all duration-100"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={onClick}
      />
      {hovered && (
        <g>
          {/* Tooltip background */}
          <rect
            x={x + width / 2 - 80}
            y={y - 50}
            width={160}
            height={42}
            rx={4}
            fill="var(--color-bg-elevated, #1e293b)"
            stroke="var(--color-border, #334155)"
            strokeWidth={1}
            className="pointer-events-none"
          />
          {/* Tooltip text */}
          <text
            x={x + width / 2}
            y={y - 34}
            textAnchor="middle"
            fill="currentColor"
            className="text-[10px] text-text-primary pointer-events-none"
          >
            {workflowName} on {formattedDate}
          </text>
          <text
            x={x + width / 2}
            y={y - 18}
            textAnchor="middle"
            fill="currentColor"
            className="text-[9px] text-text-muted pointer-events-none"
          >
            {totalRuns} runs, {passed} passed, {failed} failed ({(successRate * 100).toFixed(0)}%)
          </text>
        </g>
      )}
    </g>
  );
}
