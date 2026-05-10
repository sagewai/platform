'use client';

import { useMemo } from 'react';
import {
  Area,
  AreaChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useMissionEventHistory } from '@/lib/mission-events/provider';

interface BurnPoint {
  t: number;
  cost: number;
}

type Band = 'green' | 'yellow' | 'red' | 'none';

const BAND_VAR: Record<Band, string> = {
  green: 'var(--color-success)',
  yellow: 'var(--color-warning)',
  red: 'var(--color-error)',
  none: 'var(--color-text-secondary)',
};

export function CostBurnDownChart({ capUsd }: { capUsd: number | null | undefined }) {
  const events = useMissionEventHistory();

  const { data, totalCost, band } = useMemo(() => {
    let cum = 0;
    const data: BurnPoint[] = [];
    const t0 = events[0] ? new Date(events[0].ts).getTime() : 0;
    for (const e of events) {
      if (e.kind === 'agent.llm_call' && typeof e.cost_usd === 'number') {
        cum += e.cost_usd;
        data.push({ t: (new Date(e.ts).getTime() - t0) / 1000, cost: cum });
      } else if (e.kind === 'agent.tool_result' && typeof e.cost_usd === 'number' && e.cost_usd > 0) {
        cum += e.cost_usd;
        data.push({ t: (new Date(e.ts).getTime() - t0) / 1000, cost: cum });
      } else if (e.kind === 'mission.finished' && typeof e.total_cost_usd === 'number') {
        cum = e.total_cost_usd;
        data.push({ t: (new Date(e.ts).getTime() - t0) / 1000, cost: cum });
      }
    }
    let band: Band = 'none';
    if (capUsd && capUsd > 0) {
      const ratio = cum / capUsd;
      band = ratio < 0.5 ? 'green' : ratio < 0.8 ? 'yellow' : 'red';
    }
    return { data, totalCost: cum, band };
  }, [events, capUsd]);

  const stroke = BAND_VAR[band];
  const cap = capUsd ?? 0;

  return (
    <figure
      role="group"
      aria-label="Cost burn-down"
      data-testid="cost-burn-down"
      data-band={band}
      data-total-usd={totalCost.toFixed(6)}
      className="flex flex-col gap-2 m-0"
    >
      <div className="flex items-baseline justify-between text-xs text-text-secondary">
        <span className="font-medium uppercase tracking-wide">Cost burn-down</span>
        <span className="font-mono tabular-nums text-text-primary">
          ${totalCost.toFixed(4)}
          {cap > 0 && (
            <span className="text-text-muted"> / ${cap.toFixed(2)}</span>
          )}
        </span>
      </div>
      <div className="h-32 w-full" aria-hidden="true">
        <ResponsiveContainer>
          <AreaChart
            data={data}
            margin={{ top: 8, right: 8, left: 8, bottom: 8 }}
          >
            <defs>
              <linearGradient id="cost-burn-grad" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.45} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="t"
              type="number"
              tickFormatter={(s: number) => `${Math.round(s)}s`}
              stroke="currentColor"
              className="text-text-muted text-[10px]"
            />
            <YAxis
              tickFormatter={(v: number) => `$${v.toFixed(2)}`}
              stroke="currentColor"
              className="text-text-muted text-[10px]"
            />
            <Tooltip
              formatter={(value) => [`$${Number(value).toFixed(4)}`, 'cumulative']}
              labelFormatter={(label) => `${Math.round(Number(label))}s`}
            />
            {cap > 0 && (
              <ReferenceLine
                y={cap}
                stroke="currentColor"
                strokeDasharray="4 4"
                className="text-text-muted"
                label={{
                  value: `cap`,
                  position: 'right',
                  fill: 'currentColor',
                  fontSize: 10,
                }}
              />
            )}
            <Area
              type="monotone"
              dataKey="cost"
              stroke={stroke}
              fill="url(#cost-burn-grad)"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {/* Screen-reader fallback table */}
      <table className="sr-only">
        <caption>Cost burn-down data points</caption>
        <thead>
          <tr>
            <th scope="col">Time (s)</th>
            <th scope="col">Cumulative spend (USD)</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.t}>
              <td>{Math.round(d.t)}s</td>
              <td>${d.cost.toFixed(4)}</td>
            </tr>
          ))}
          {data.length === 0 && (
            <tr>
              <td colSpan={2}>No data yet.</td>
            </tr>
          )}
        </tbody>
        {cap > 0 && (
          <tfoot>
            <tr>
              <td>Budget cap</td>
              <td>${cap.toFixed(2)}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </figure>
  );
}
