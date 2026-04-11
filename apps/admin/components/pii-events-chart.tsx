'use client';

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { Card, EmptyState } from '@/components/ui/legacy';

interface PIIEventPoint {
  date: string;
  pii: number;
  hallucination: number;
  content_filter: number;
}

interface PIIEventsChartProps {
  data: PIIEventPoint[];
}

export function PIIEventsChart({ data }: PIIEventsChartProps) {
  if (data.length === 0) {
    return (
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
          Guardrail Events Over Time
        </h3>
        <EmptyState title="No Data" description="No events recorded yet." />
      </Card>
    );
  }

  return (
    <Card>
      <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
        Guardrail Events Over Time
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis dataKey="date" tick={{ fontSize: 12 }} stroke="var(--color-text-muted)" />
          <YAxis tick={{ fontSize: 12 }} stroke="var(--color-text-muted)" />
          <Tooltip contentStyle={{ borderRadius: 6, fontSize: 13 }} />
          <Legend />
          <Line
            type="monotone"
            dataKey="pii"
            name="PII Detections"
            stroke="#dc3545"
            strokeWidth={2}
            dot={{ r: 3 }}
          />
          <Line
            type="monotone"
            dataKey="hallucination"
            name="Hallucination Flags"
            stroke="#ffc107"
            strokeWidth={2}
            dot={{ r: 3 }}
          />
          <Line
            type="monotone"
            dataKey="content_filter"
            name="Content Filter"
            stroke="var(--color-primary)"
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}
