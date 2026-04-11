'use client';

import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from 'recharts';
import { LineChart as LineChartIcon } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart';
import { EmptyState } from '@/components/ui/empty-state';

interface CostTrendPoint {
  date: string;
  cost: number;
}

interface CostTrendChartProps {
  data: CostTrendPoint[];
}

const config = {
  cost: {
    label: 'Cost',
    color: 'var(--chart-1)',
  },
} satisfies ChartConfig;

export function CostTrendChart({ data }: CostTrendChartProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base font-semibold">Cost Trend</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <EmptyState
            icon={LineChartIcon}
            title="No cost data yet"
            description="Once your agents start running, cost trends will appear here."
            className="border-0 py-6"
          />
        ) : (
          <ChartContainer config={config} className="h-72 w-full">
            <AreaChart data={data} margin={{ left: 8, right: 8, top: 8, bottom: 0 }}>
              <defs>
                <linearGradient id="costFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12, fill: 'var(--muted-foreground)' }}
                tickFormatter={(v: number) => `$${v.toFixed(2)}`}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    formatter={(value) => [`$${Number(value ?? 0).toFixed(4)}`, 'Cost']}
                  />
                }
              />
              <Area
                type="monotone"
                dataKey="cost"
                stroke="var(--chart-1)"
                strokeWidth={2}
                fill="url(#costFill)"
              />
            </AreaChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  );
}
