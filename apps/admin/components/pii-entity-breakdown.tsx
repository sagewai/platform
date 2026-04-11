'use client';

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { Card, EmptyState } from '@/components/ui/legacy';

interface EntityEntry {
  entity: string;
  count: number;
}

interface PIIEntityBreakdownProps {
  data: EntityEntry[];
}

const COLORS = ['#dc3545', '#ff6b6b', '#e77f67', '#ffc107', '#ffd93d', '#4ecdc4', '#6c63ff'];

export function PIIEntityBreakdown({ data }: PIIEntityBreakdownProps) {
  if (data.length === 0) {
    return (
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
          Entity Type Breakdown
        </h3>
        <EmptyState title="No Data" description="No PII entities detected yet." />
      </Card>
    );
  }

  return (
    <Card>
      <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
        Entity Type Breakdown
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            outerRadius={100}
            dataKey="count"
            nameKey="entity"
            label={((props: { name?: string; percent?: number }) =>
              `${props.name ?? ''} (${((props.percent ?? 0) * 100).toFixed(0)}%)`) as unknown as boolean
            }
            labelLine={false}
          >
            {data.map((_entry, index) => (
              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value) => [value, 'Detections']}
            contentStyle={{ borderRadius: 6, fontSize: 13 }}
          />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </Card>
  );
}
