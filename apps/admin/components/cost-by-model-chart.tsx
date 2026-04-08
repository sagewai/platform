'use client';

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card, EmptyState } from '@sagecurator/ui';

interface CostByModelEntry {
  model: string;
  cost: number;
}

interface CostByModelChartProps {
  data: CostByModelEntry[];
}

export function CostByModelChart({ data }: CostByModelChartProps) {
  if (data.length === 0) {
    return (
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Cost by Model</h3>
        <EmptyState title="No Data" description="No cost data available." />
      </Card>
    );
  }

  return (
    <Card>
      <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Cost by Model</h3>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} layout="vertical" margin={{ left: 100 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            type="number"
            tick={{ fontSize: 12 }}
            stroke="var(--color-text-muted)"
            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
          />
          <YAxis
            type="category"
            dataKey="model"
            tick={{ fontSize: 12 }}
            stroke="var(--color-text-muted)"
            width={100}
          />
          <Tooltip
            formatter={(value) => [`$${Number(value ?? 0).toFixed(4)}`, 'Cost']}
            contentStyle={{ borderRadius: 6, fontSize: 13 }}
          />
          <Bar dataKey="cost" fill="var(--color-primary)" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </Card>
  );
}
