'use client';

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card, EmptyState } from '@sagecurator/ui';

interface CostTrendPoint {
  date: string;
  cost: number;
}

interface CostTrendChartProps {
  data: CostTrendPoint[];
}

export function CostTrendChart({ data }: CostTrendChartProps) {
  if (data.length === 0) {
    return (
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Cost Trend</h3>
        <EmptyState title="No Data" description="No cost data available yet." />
      </Card>
    );
  }

  return (
    <Card>
      <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Cost Trend</h3>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis dataKey="date" tick={{ fontSize: 12 }} stroke="var(--color-text-muted)" />
          <YAxis
            tick={{ fontSize: 12 }}
            stroke="var(--color-text-muted)"
            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
          />
          <Tooltip
            formatter={(value) => [`$${Number(value ?? 0).toFixed(4)}`, 'Cost']}
            contentStyle={{ borderRadius: 8, fontSize: 13, backgroundColor: '#111B2E', border: '1px solid #1E3A5F', color: '#E8EAED' }}
            labelStyle={{ color: '#9AA0A6' }}
          />
          <Line
            type="monotone"
            dataKey="cost"
            stroke="var(--color-primary)"
            strokeWidth={2}
            dot={{ r: 3 }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}
