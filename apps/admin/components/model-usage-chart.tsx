'use client';

import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts';
import { BarChart3 } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart';
import { EmptyState } from '@/components/ui/empty-state';

interface ModelUsageEntry {
  model: string;
  tokens: number;
}

interface ModelUsageChartProps {
  data: ModelUsageEntry[];
}

const config = {
  tokens: {
    label: 'Tokens',
    color: 'var(--chart-2)',
  },
} satisfies ChartConfig;

export function ModelUsageChart({ data }: ModelUsageChartProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base font-semibold">Token Usage by Model</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <EmptyState
            icon={BarChart3}
            title="No usage data yet"
            description="Token usage by model will appear once agents start running."
            className="border-0 py-6"
          />
        ) : (
          <ChartContainer config={config} className="h-72 w-full">
            <BarChart data={data} margin={{ left: 8, right: 8, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="model"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }}
                interval={0}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }}
                tickFormatter={(v: number) => v.toLocaleString()}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    formatter={(value) => [Number(value ?? 0).toLocaleString(), 'Tokens']}
                  />
                }
              />
              <Bar dataKey="tokens" fill="var(--chart-2)" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  );
}
