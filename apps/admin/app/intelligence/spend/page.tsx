'use client';

import { useCallback, useEffect, useState } from 'react';
import { Badge, Card } from '@/components/ui/legacy';
import type { BadgeVariant } from '@/components/ui/legacy';
import {
  DollarSign,
  Cpu,
  Wifi,
  WifiOff,
  RefreshCw,
  BarChart3,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts';
import { authFetch } from '@/utils/auth';

const API_BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace(/\/admin$/, '') ?? '';

const POLL_INTERVAL = 60_000; // 60 seconds

interface SpendData {
  global_spend: Record<string, unknown>;
  by_model: Array<Record<string, unknown>>;
  model_count: number;
}

type ConnectionStatus = 'connected' | 'disconnected' | 'loading';

const STATUS_BADGE: Record<ConnectionStatus, BadgeVariant> = {
  connected: 'success',
  disconnected: 'error',
  loading: 'default',
};

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connected: 'Connected',
  disconnected: 'Disconnected',
  loading: 'Loading...',
};

export default function LLMSpendPage() {
  const [data, setData] = useState<SpendData | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>('loading');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchSpend = useCallback(async () => {
    try {
      const resp = await authFetch(`${API_BASE}/api/v1/organization/litellm-spend`);
      if (!resp.ok) {
        setStatus('disconnected');
        return;
      }
      const json = await resp.json();
      if (json.global_spend?.error) {
        setStatus('disconnected');
        setData(json);
        return;
      }
      setData(json);
      setStatus('connected');
      setLastUpdated(new Date());
    } catch {
      setStatus('disconnected');
    }
  }, []);

  useEffect(() => {
    fetchSpend();
    const id = setInterval(() => {
      // Pause polling when the tab is hidden to avoid unnecessary proxy load
      if (document.visibilityState === 'visible') {
        fetchSpend();
      }
    }, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchSpend]);

  // Extract total spend value from global_spend response
  const totalSpend = (() => {
    if (!data?.global_spend) return null;
    // LiteLLM /global/spend returns various shapes; extract a numeric total
    const gs = data.global_spend;
    if (typeof gs === 'number') return gs;
    if (typeof gs['total_spend'] === 'number') return gs['total_spend'] as number;
    if (typeof gs['spend'] === 'number') return gs['spend'] as number;
    return null;
  })();

  // Prepare chart data from by_model breakdown
  const chartData = (data?.by_model ?? []).map((row) => ({
    model: (row['model'] as string) ?? 'unknown',
    spend: typeof row['spend'] === 'number' ? row['spend'] : 0,
    tokens: typeof row['total_tokens'] === 'number' ? row['total_tokens'] : 0,
  }));

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
            LLM Spend
          </h1>
          <p className="mt-0 text-sm text-text-secondary">
            Real-time spend tracking from your LiteLLM proxy. Auto-refreshes every 60 seconds.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={STATUS_BADGE[status]}>
            <span className="flex items-center gap-1.5">
              {status === 'connected' ? <Wifi size={10} /> : <WifiOff size={10} />}
              {STATUS_LABEL[status]}
            </span>
          </Badge>
          <button
            onClick={fetchSpend}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-text-secondary border border-border rounded-md hover:bg-bg-subtle transition-colors"
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-lg">
        <Card className="!p-4">
          <div className="flex items-center gap-2 mb-1">
            <DollarSign size={16} className="text-text-muted" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Total Spend</span>
          </div>
          <div className="text-2xl font-bold text-text-primary">
            {totalSpend !== null ? `$${totalSpend.toFixed(2)}` : '--'}
          </div>
          <div className="text-xs text-text-muted mt-0.5">
            {lastUpdated
              ? `Updated ${lastUpdated.toLocaleTimeString('en-US', {
                  hour: '2-digit',
                  minute: '2-digit',
                })}`
              : 'Not yet loaded'}
          </div>
        </Card>

        <Card className="!p-4">
          <div className="flex items-center gap-2 mb-1">
            <Cpu size={16} className="text-text-muted" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Models Available</span>
          </div>
          <div className="text-2xl font-bold text-text-primary">
            {data?.model_count ?? '--'}
          </div>
          <div className="text-xs text-text-muted mt-0.5">via LiteLLM proxy</div>
        </Card>

        <Card className="!p-4">
          <div className="flex items-center gap-2 mb-1">
            <BarChart3 size={16} className="text-text-muted" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Models Used</span>
          </div>
          <div className="text-2xl font-bold text-text-primary">
            {chartData.length > 0 ? chartData.length : '--'}
          </div>
          <div className="text-xs text-text-muted mt-0.5">with recorded spend</div>
        </Card>
      </div>

      {/* Spend by model chart */}
      {chartData.length > 0 && (
        <Card className="mb-lg">
          <h2 className="text-lg font-bold font-[family-name:var(--font-heading)] text-text-primary mt-0 mb-md">
            Spend by Model
          </h2>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 5, right: 20, bottom: 60, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="model"
                  tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
                  angle={-35}
                  textAnchor="end"
                  interval={0}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: 'var(--color-text-muted)' }}
                  tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--color-bg-elevated)',
                    border: '1px solid var(--color-border)',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                  formatter={(value) => [`$${Number(value ?? 0).toFixed(4)}`, 'Spend']}
                  labelStyle={{ fontWeight: 600 }}
                />
                <Bar dataKey="spend" fill="var(--color-primary)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {/* Spend by model table */}
      <Card>
        <h2 className="text-lg font-bold font-[family-name:var(--font-heading)] text-text-primary mt-0 mb-md">
          Model Breakdown
        </h2>
        {chartData.length === 0 ? (
          <div className="py-8 text-center text-sm text-text-muted">
            {status === 'disconnected'
              ? 'LiteLLM proxy is not connected. Configure it in Settings > Organization.'
              : 'No spend data available yet.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-border">
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Model
                  </th>
                  <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Spend
                  </th>
                  <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Tokens
                  </th>
                </tr>
              </thead>
              <tbody>
                {chartData
                  .sort((a, b) => b.spend - a.spend)
                  .map((row) => (
                    <tr
                      key={row.model}
                      className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors"
                    >
                      <td className="py-2.5 px-3 font-medium text-text-primary">
                        {row.model}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-text-secondary">
                        ${row.spend.toFixed(4)}
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono text-text-secondary">
                        {row.tokens > 0 ? row.tokens.toLocaleString() : '--'}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
