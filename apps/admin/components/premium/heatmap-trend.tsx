'use client';

import {
  ResponsiveContainer,
  LineChart,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ComposedChart,
} from 'recharts';

interface HeatmapDataPoint {
  workflow_name: string;
  date: string;
  total_runs: number;
  passed: number;
  failed: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
}

interface HeatmapTrendProps {
  data: HeatmapDataPoint[];
  selectedWorkflow?: string;
}

export function HeatmapTrend({ data, selectedWorkflow }: HeatmapTrendProps) {
  // Aggregate by date — avg_duration and p95 across selected workflow(s)
  const filtered = selectedWorkflow
    ? data.filter((d) => d.workflow_name === selectedWorkflow)
    : data;

  const byDate = new Map<string, { avg: number[]; p95: number[] }>();
  for (const d of filtered) {
    const entry = byDate.get(d.date) ?? { avg: [], p95: [] };
    entry.avg.push(d.avg_duration_ms);
    entry.p95.push(d.p95_duration_ms);
    byDate.set(d.date, entry);
  }

  const chartData = Array.from(byDate.entries())
    .map(([date, { avg, p95 }]) => ({
      date,
      avg_ms: Math.round(avg.reduce((a, b) => a + b, 0) / avg.length),
      p95_ms: Math.round(Math.max(...p95)),
    }))
    .sort((a, b) => a.date.localeCompare(b.date));

  if (chartData.length === 0) {
    return (
      <div className="text-xs text-text-muted text-center py-4">
        No duration data available for trend chart.
      </div>
    );
  }

  return (
    <div className="h-[200px]">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border, #334155)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: 'var(--color-text-muted, #94a3b8)' }}
            tickFormatter={(v: string) => {
              const d = new Date(v + 'T00:00:00');
              return `${d.getMonth() + 1}/${d.getDate()}`;
            }}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: 'var(--color-text-muted, #94a3b8)' }}
            tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--color-bg-elevated, #1e293b)',
              border: '1px solid var(--color-border, #334155)',
              borderRadius: '6px',
              fontSize: '11px',
            }}
            formatter={(value, name) => {
              const v = Number(value ?? 0);
              return [
                v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v}ms`,
                String(name) === 'avg_ms' ? 'Avg Duration' : 'P95 Duration',
              ];
            }}
            labelFormatter={(label) => {
              const d = new Date(label + 'T00:00:00');
              return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
            }}
          />
          <Area
            type="monotone"
            dataKey="p95_ms"
            fill="#3b82f6"
            fillOpacity={0.15}
            stroke="none"
          />
          <Line
            type="monotone"
            dataKey="avg_ms"
            stroke="#22c55e"
            strokeWidth={2}
            dot={false}
            name="avg_ms"
          />
          <Line
            type="monotone"
            dataKey="p95_ms"
            stroke="#3b82f6"
            strokeWidth={1.5}
            strokeDasharray="4 2"
            dot={false}
            name="p95_ms"
          />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="flex items-center justify-center gap-4 mt-1 text-[10px] text-text-muted">
        <span className="flex items-center gap-1">
          <span className="w-4 h-0.5 inline-block" style={{ background: '#22c55e' }} />
          Avg Duration
        </span>
        <span className="flex items-center gap-1">
          <span
            className="w-4 h-0.5 inline-block"
            style={{ background: '#3b82f6', borderTop: '1px dashed #3b82f6' }}
          />
          P95 Duration (shaded)
        </span>
      </div>
    </div>
  );
}
