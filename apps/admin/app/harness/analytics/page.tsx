'use client';

import { useState, useEffect } from 'react';
import { BarChart3, DollarSign, Users, Zap, TrendingDown, Calendar } from 'lucide-react';
const DEMO_BY_MODEL = [
  { model: 'claude-haiku-4-5-20251001', label: 'Haiku', requests: 847, cost: 3.21, tokens: 4_200_000, pct: 66, color: 'bg-emerald-500' },
  { model: 'claude-sonnet-4-5-20250929', label: 'Sonnet', requests: 312, cost: 28.45, tokens: 3_100_000, pct: 24, color: 'bg-amber-500' },
  { model: 'claude-opus-4-6', label: 'Opus', requests: 125, cost: 112.30, tokens: 1_800_000, pct: 10, color: 'bg-red-500' },
];

const DEMO_TOP_USERS = [
  { user: 'alice', requests: 423, cost: 48.21, avg_tier: 'medium' },
  { user: 'charlie', requests: 312, cost: 22.14, avg_tier: 'simple' },
  { user: 'bob', requests: 287, cost: 15.67, avg_tier: 'simple' },
  { user: 'diana', requests: 178, cost: 42.89, avg_tier: 'complex' },
  { user: 'ci-bot', requests: 84, cost: 14.05, avg_tier: 'simple' },
];

const PERIODS = ['Today', '7d', '30d', '90d'] as const;

const DEMO_SAVINGS = {
  totalWithoutHarness: 287.34,
  totalWithHarness: 143.96,
  saved: 143.38,
  pctSaved: 49.9,
  routedDown: 1_159,
  totalRequests: 1_284,
};

const TIER_BADGE: Record<string, string> = {
  simple: 'bg-emerald-500/10 text-emerald-400',
  medium: 'bg-amber-500/10 text-amber-400',
  complex: 'bg-red-500/10 text-red-400',
};

export default function HarnessAnalyticsPage() {
  const [period, setPeriod] = useState<typeof PERIODS[number]>('30d');
  const [byModel, setByModel] = useState(DEMO_BY_MODEL);
  const [topUsers, setTopUsers] = useState(DEMO_TOP_USERS);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch('/api/v1/harness/spend/breakdown').then(r => r.ok ? r.json() : null),
      fetch('/api/v1/harness/spend').then(r => r.ok ? r.json() : null),
    ])
      .then(([breakdown, spendData]) => {
        if (breakdown && Object.keys(breakdown).length > 0) {
          const models = Object.entries(breakdown as Record<string, Record<string, number>>).map(
            ([model, data]) => ({
              model,
              label: model.split('-').pop() || model,
              requests: data.requests ?? 0,
              cost: data.cost_usd ?? 0,
              tokens: (data.input_tokens ?? 0) + (data.output_tokens ?? 0),
              pct: 0,
              color: 'bg-cyan-500',
            })
          );
          if (models.length > 0) setByModel(models);
        }
        if (spendData && typeof spendData === 'object') {
          // Top users from spend data (if backend provides per-user breakdown)
          const users = Object.entries((spendData.by_user ?? {}) as Record<string, Record<string, number>>).map(
            ([user, data]) => ({
              user,
              requests: data.requests ?? 0,
              cost: data.cost_usd ?? 0,
              avg_tier: 'medium' as const,
            })
          );
          if (users.length > 0) setTopUsers(users);
        }
      })
      .catch(() => { /* keep demo data */ })
      .finally(() => setLoading(false));
  }, [period]);

  return (
    <div className="mx-auto max-w-[72rem] space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100">Spend Analytics</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Track LLM costs and measure routing savings across your organization.
          </p>
        </div>
        <div className="flex gap-1 bg-zinc-800 rounded-lg p-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1.5 text-xs rounded-md transition ${
                period === p
                  ? 'bg-zinc-700 text-zinc-100'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Savings highlight */}
      <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-xl p-6">
        <div className="flex items-center gap-3 mb-3">
          <TrendingDown className="w-5 h-5 text-emerald-400" />
          <h2 className="text-sm font-medium text-emerald-300">Cost Savings Estimate</h2>
        </div>
        <div className="grid grid-cols-2 gap-6 lg:grid-cols-4">
          <div>
            <div className="text-2xl font-bold text-emerald-400">
              ${DEMO_SAVINGS.saved.toFixed(2)}
            </div>
            <div className="text-xs text-emerald-500 mt-1">saved this period</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-zinc-200">
              {DEMO_SAVINGS.pctSaved.toFixed(0)}%
            </div>
            <div className="text-xs text-zinc-500 mt-1">cost reduction</div>
          </div>
          <div>
            <div className="text-lg font-semibold text-zinc-300">
              ${DEMO_SAVINGS.totalWithoutHarness.toFixed(2)}
            </div>
            <div className="text-xs text-zinc-500 mt-1">without harness</div>
          </div>
          <div>
            <div className="text-lg font-semibold text-zinc-300">
              ${DEMO_SAVINGS.totalWithHarness.toFixed(2)}
            </div>
            <div className="text-xs text-zinc-500 mt-1">with harness</div>
          </div>
        </div>
        <div className="text-xs text-emerald-600 mt-3">
          {DEMO_SAVINGS.routedDown.toLocaleString()} of {DEMO_SAVINGS.totalRequests.toLocaleString()} requests
          routed to cheaper models ({((DEMO_SAVINGS.routedDown / DEMO_SAVINGS.totalRequests) * 100).toFixed(0)}%)
        </div>
      </div>

      {/* Spend by Model */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
        <h2 className="text-sm font-medium text-zinc-300 mb-4">Spend by Model</h2>
        <div className="space-y-3">
          {DEMO_BY_MODEL.map((m) => (
            <div key={m.model} className="flex items-center gap-4">
              <div className="w-20 text-sm font-medium text-zinc-300">{m.label}</div>
              <div className="flex-1">
                <div className="flex gap-1 h-5 bg-zinc-800 rounded-full overflow-hidden">
                  <div className={`${m.color} rounded-full transition-all`} style={{ width: `${Math.max(m.pct, 3)}%` }} />
                </div>
              </div>
              <div className="w-24 text-right text-xs text-zinc-400">
                ${m.cost.toFixed(2)}
              </div>
              <div className="w-20 text-right text-xs text-zinc-500">
                {m.requests} reqs
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Top Spenders */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
        <h2 className="text-sm font-medium text-zinc-300 mb-4">Top Spenders</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
              <th className="pb-2 pr-4">#</th>
              <th className="pb-2 pr-4">User</th>
              <th className="pb-2 pr-4">Requests</th>
              <th className="pb-2 pr-4">Cost</th>
              <th className="pb-2">Avg Tier</th>
            </tr>
          </thead>
          <tbody>
            {DEMO_TOP_USERS.map((u, i) => (
              <tr key={u.user} className="border-b border-zinc-800/50">
                <td className="py-2 pr-4 text-zinc-500">{i + 1}</td>
                <td className="py-2 pr-4 text-zinc-200 font-medium">{u.user}</td>
                <td className="py-2 pr-4 text-zinc-400">{u.requests.toLocaleString()}</td>
                <td className="py-2 pr-4 text-zinc-300">${u.cost.toFixed(2)}</td>
                <td className="py-2">
                  <span className={`inline-block px-2 py-0.5 text-[10px] rounded-full ${TIER_BADGE[u.avg_tier] || ''}`}>
                    {u.avg_tier}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
