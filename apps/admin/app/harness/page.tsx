'use client';

import { useState, useEffect } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Gauge, Zap, DollarSign, Activity, BarChart3 } from 'lucide-react';

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

export default function HarnessDashboardPage() {
  const [spend, setSpend] = useState<SpendSummary | null>(null);
  const [byModel, setByModel] = useState<ModelRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch('/api/v1/harness/spend').then((r) => (r.ok ? r.json() : null)),
      fetch('/api/v1/harness/spend/breakdown').then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([summary, breakdown]) => {
        if (summary) setSpend(summary as SpendSummary);
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
        <h1 className="text-2xl font-semibold text-zinc-100">LLM Harness</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Smart proxy routing AI coding tools to the optimal model per request.
        </p>
      </div>

      {/* KPI Cards — backed by /spend */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard
          icon={Activity}
          label="Requests Today"
          value={loading ? '—' : (spend?.daily_requests ?? 0).toLocaleString()}
        />
        <KpiCard
          icon={DollarSign}
          label="Cost Today"
          value={loading ? '—' : `$${(spend?.daily_cost_usd ?? 0).toFixed(2)}`}
        />
        <KpiCard
          icon={Zap}
          label="Requests (30d)"
          value={loading ? '—' : (spend?.monthly_requests ?? 0).toLocaleString()}
        />
        <KpiCard
          icon={Gauge}
          label="Cost (30d)"
          value={loading ? '—' : `$${(spend?.monthly_cost_usd ?? 0).toFixed(2)}`}
        />
      </div>

      {/* Spend by Model — backed by /spend/breakdown */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 className="w-4 h-4 text-zinc-400" />
          <h2 className="text-sm font-medium text-zinc-300">Spend by Model</h2>
        </div>
        {loading ? (
          <div className="text-sm text-zinc-500">Loading…</div>
        ) : byModel.length === 0 ? (
          <div className="text-sm text-zinc-500">
            No harness spend recorded yet. Routing activity will appear here once requests flow through the proxy.
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
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function KpiCard({ icon: Icon, label, value, sub, accent }: {
  icon: LucideIcon; label: string; value: string; sub?: string; accent?: string;
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <div className="flex items-center gap-2 text-xs text-zinc-500 mb-2">
        <Icon className="w-3.5 h-3.5" />
        {label}
      </div>
      <div className={`text-xl font-semibold ${accent || 'text-zinc-100'}`}>{value}</div>
      {sub && <div className="text-[10px] text-zinc-600 mt-1">{sub}</div>}
    </div>
  );
}
