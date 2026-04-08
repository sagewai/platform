'use client';

import { useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { HeatmapCell } from './heatmap-cell';

interface HeatmapDataPoint {
  workflow_name: string;
  date: string;
  total_runs: number;
  passed: number;
  failed: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
}

interface PerformanceHeatmapProps {
  data: HeatmapDataPoint[];
  days: number;
}

const CELL_SIZE = 14;
const CELL_GAP = 2;
const LABEL_WIDTH = 140;
const TOP_MARGIN = 30;

export function PerformanceHeatmap({ data, days }: PerformanceHeatmapProps) {
  const router = useRouter();

  const { workflowNames, dates, lookup, maxRuns } = useMemo(() => {
    const names = new Set<string>();
    const dateSet = new Set<string>();
    const map = new Map<string, HeatmapDataPoint>();
    let max = 1;

    for (const d of data) {
      names.add(d.workflow_name);
      dateSet.add(d.date);
      map.set(`${d.workflow_name}|${d.date}`, d);
      if (d.total_runs > max) max = d.total_runs;
    }

    // Sort workflows alphabetically, dates chronologically
    const sortedNames = Array.from(names).sort();
    const sortedDates = Array.from(dateSet).sort();

    return {
      workflowNames: sortedNames,
      dates: sortedDates,
      lookup: map,
      maxRuns: max,
    };
  }, [data]);

  if (!workflowNames.length || !dates.length) {
    return (
      <div className="text-sm text-text-muted text-center py-8">
        No workflow run data available for the selected time range.
      </div>
    );
  }

  const svgWidth = LABEL_WIDTH + dates.length * (CELL_SIZE + CELL_GAP) + 20;
  const svgHeight = TOP_MARGIN + workflowNames.length * (CELL_SIZE + CELL_GAP) + 20;

  // Show date labels at intervals
  const dateInterval = Math.max(1, Math.floor(dates.length / 10));

  return (
    <div className="overflow-x-auto">
      <svg width={svgWidth} height={svgHeight} className="min-w-full">
        {/* Date labels along top */}
        {dates.map((date, i) => {
          if (i % dateInterval !== 0) return null;
          const x = LABEL_WIDTH + i * (CELL_SIZE + CELL_GAP) + CELL_SIZE / 2;
          const formatted = new Date(date + 'T00:00:00').toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
          });
          return (
            <text
              key={date}
              x={x}
              y={TOP_MARGIN - 8}
              textAnchor="middle"
              fill="currentColor"
              className="text-[8px] text-text-muted"
            >
              {formatted}
            </text>
          );
        })}

        {/* Workflow name labels + cells */}
        {workflowNames.map((name, rowIdx) => {
          const y = TOP_MARGIN + rowIdx * (CELL_SIZE + CELL_GAP);
          return (
            <g key={name}>
              {/* Label */}
              <text
                x={LABEL_WIDTH - 8}
                y={y + CELL_SIZE / 2}
                dy="0.35em"
                textAnchor="end"
                fill="currentColor"
                className="text-[10px] text-text-muted"
              >
                {name.length > 18 ? name.slice(0, 18) + '\u2026' : name}
              </text>

              {/* Cells */}
              {dates.map((date, colIdx) => {
                const point = lookup.get(`${name}|${date}`);
                if (!point) {
                  // Empty cell
                  return (
                    <rect
                      key={date}
                      x={LABEL_WIDTH + colIdx * (CELL_SIZE + CELL_GAP)}
                      y={y}
                      width={CELL_SIZE}
                      height={CELL_SIZE}
                      fill="currentColor"
                      fillOpacity={0.05}
                      rx={2}
                      className="text-text-muted"
                    />
                  );
                }

                return (
                  <HeatmapCell
                    key={date}
                    x={LABEL_WIDTH + colIdx * (CELL_SIZE + CELL_GAP)}
                    y={y}
                    width={CELL_SIZE}
                    height={CELL_SIZE}
                    workflowName={name}
                    date={date}
                    totalRuns={point.total_runs}
                    passed={point.passed}
                    failed={point.failed}
                    maxRuns={maxRuns}
                    onClick={() =>
                      router.push(
                        `/workflows/history?status=failed&search=${encodeURIComponent(name)}`,
                      )
                    }
                  />
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
