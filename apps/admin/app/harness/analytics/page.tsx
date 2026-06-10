'use client';

import { useState, useEffect } from 'react';
import { DollarSign } from 'lucide-react';

interface SpendSummary {
  daily_cost_usd: number;
  daily_requests: number;
  monthly_cost_usd: number;
  monthly_requests: number;
  total_cost_usd: number;
  total_requests: number;
}

interface ModelBreakdownEntry {
  cost_usd: number;
  requests: number;
  input_tokens: number;
  output_tokens: number;
}

interface ModelRow {
  model: string;
  label: string;
  requests: number;
  cost: number;
  tokens: number;
}

function shortLabel(model: string): string {
  if (model.includes('haiku')) return 'Haiku';
  if (model.includes('sonnet')) return 'Sonnet';
  if (model.includes('opus')) return 'Opus';
  if (model.includes('gpt-4o-mini')) return 'GPT-4o Mini';
  if (model.includes('gpt-4o')) return 'GPT-4o';
  return model.split('-').slice(0, 3).join('-') || model;
}

export default function HarnessAnalyticsPage() {
  const [summary, setSummary] = useState<SpendSummary | null>(null);
  const [byModel, setByModel] = useState<ModelRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch('/api/v1/harness/spend/breakdown').then((r) => (r.ok ? r.json() : null)),
      fetch('/api/v1/harness/spend').then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([breakdown, spend]) => {
        if (spend) setSummary(spend as SpendSummary);
        if (breakdown && typeof breakdown === 'object') {
          const rows = Object.entries(breakdown as Record<string, ModelBreakdownEntry>)
            .map(([model, data]) => ({
              model,
              label: shortLabel(model),
              requests: data.requests ?? 0,
              cost: data.cost_usd ?? 0,
              tokens: (data.input_tokens ?? 0) + (data.output_tokens ?? 0),
            }))
            .sort((a, b) => b.cost - a.cost);
          setByModel(rows);
        }
      })
      .catch(() => { /* leave empty */ })
      .finally(() => setLoading(false));
  }, []);

  const maxCost = byModel.reduce((m, r) => Math.max(m, r.cost), 0);

  return (
    <div className="mx-auto max-w-[72rem] space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-100">Spend Analytics</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Track LLM costs by model across your organization.
        </p>
      </div>

      {/* Spend summary — backed by /spend */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <SummaryCard label="Cost Today" value={loading ? '—' : `$${(summary?.daily_cost_usd ?? 0).toFixed(2)}`} sub={loading ? '' : `${(summary?.daily_requests ?? 0).toLocaleString()} requests`} />
        <SummaryCard label="Cost (30d)" value={loading ? '—' : `$${(summary?.monthly_cost_usd ?? 0).toFixed(2)}`} sub={loading ? '' : `${(summary?.monthly_requests ?? 0).toLocaleString()} requests`} />
        <SummaryCard label="Cost (total)" value={loading ? '—' : `$${(summary?.total_cost_usd ?? 0).toFixed(2)}`} sub={loading ? '' : `${(summary?.total_requests ?? 0).toLocaleString()} requests`} />
      </div>

      {/* Spend by Model — backed by /spend/breakdown */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
        <h2 className="text-sm font-medium text-zinc-300 mb-4">Spend by Model</h2>
        {loading ? (
          <div className="text-sm text-zinc-500">Loading…</div>
        ) : byModel.length === 0 ? (
          <div className="text-sm text-zinc-500">
            No harness spend recorded yet. Per-model breakdown appears once requests flow through the proxy.
          </div>
        ) : (
          <div className="space-y-3">
            {byModel.map((m) => (
              <div key={m.model} className="flex items-center gap-4">
                <div className="w-24 text-sm font-medium text-zinc-300 truncate" title={m.model}>
                  {m.label}
                </div>
                <div className="flex-1">
                  <div className="flex h-5 bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className="bg-cyan-500 rounded-full transition-all"
                      style={{ width: `${maxCost > 0 ? Math.max((m.cost / maxCost) * 100, 3) : 3}%` }}
                    />
                  </div>
                </div>
                <div className="w-24 text-right text-xs text-zinc-400">${m.cost.toFixed(2)}</div>
                <div className="w-20 text-right text-xs text-zinc-500">{m.requests} reqs</div>
                <div className="w-28 text-right text-xs text-zinc-600">{(m.tokens / 1000).toFixed(0)}k tokens</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SummaryCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <div className="flex items-center gap-2 text-xs text-zinc-500 mb-2">
        <DollarSign className="w-3.5 h-3.5" />
        {label}
      </div>
      <div className="text-xl font-semibold text-zinc-100">{value}</div>
      {sub && <div className="text-[10px] text-zinc-600 mt-1">{sub}</div>}
    </div>
  );
}
