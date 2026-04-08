'use client';

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { Card, EmptyState } from '@sagecurator/ui';

interface ModelUsageEntry {
  model: string;
  tokens: number;
}

interface ModelUsageChartProps {
  data: ModelUsageEntry[];
}

const COLORS = ['#26C6DA', '#FFB74D', '#9C27B0', '#FF7043', '#4CAF50', '#EF5350', '#1EAFC2'];

export function ModelUsageChart({ data }: ModelUsageChartProps) {
  if (data.length === 0) {
    return (
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
          Token Usage by Model
        </h3>
        <EmptyState title="No Data" description="No usage data available yet." />
      </Card>
    );
  }

  return (
    <Card>
      <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
        Token Usage by Model
      </h3>
      <ResponsiveContainer width="100%" height={280}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            outerRadius={100}
            dataKey="tokens"
            nameKey="model"
            label={((props: { name?: string; percent?: number }) =>
              `${props.name ?? ''} (${((props.percent ?? 0) * 100).toFixed(0)}%)`) as unknown as boolean
            }
            labelLine={false}
            stroke="none"
          >
            {data.map((_entry, index) => (
              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value) => [Number(value ?? 0).toLocaleString(), 'Tokens']}
            contentStyle={{ borderRadius: 8, fontSize: 13, backgroundColor: '#111B2E', border: '1px solid #1E3A5F', color: '#E8EAED' }}
            labelStyle={{ color: '#9AA0A6' }}
          />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </Card>
  );
}
