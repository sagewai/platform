'use client';

import { useState, useEffect } from 'react';
import { Badge, Card } from '@sagecurator/ui';
import type { LucideIcon } from 'lucide-react';
import { Gauge, Zap, DollarSign, Shield, Key, Activity, ArrowDownRight, ArrowUpRight } from 'lucide-react';

const DEMO_KPI = {
  requestsToday: 1_284,
  costToday: 12.47,
  activePolicies: 4,
  activeKeys: 7,
  overrideRate: 0.68,
  savingsToday: 34.21,
};

const DEMO_TIER_DIST = [
  { tier: 'simple', count: 847, pct: 66, color: 'bg-emerald-500' },
  { tier: 'medium', count: 312, pct: 24, color: 'bg-amber-500' },
  { tier: 'complex', count: 125, pct: 10, color: 'bg-red-500' },
];

const DEMO_REQUESTS = [
  { id: '1', ts: '2m ago', user: 'alice', requested: 'claude-opus-4-6', used: 'claude-haiku-4-5-20251001', tier: 'simple', cost: 0.001, policy: 'acme-default' },
  { id: '2', ts: '5m ago', user: 'bob', requested: 'claude-opus-4-6', used: 'claude-sonnet-4-5-20250929', tier: 'medium', cost: 0.018, policy: 'intern-cap' },
  { id: '3', ts: '8m ago', user: 'alice', requested: 'claude-opus-4-6', used: 'claude-opus-4-6', tier: 'complex', cost: 0.142, policy: 'senior-access' },
  { id: '4', ts: '12m ago', user: 'charlie', requested: 'gpt-4o', used: 'gpt-4o-mini', tier: 'simple', cost: 0.0003, policy: 'acme-default' },
  { id: '5', ts: '15m ago', user: 'alice', requested: 'claude-opus-4-6', used: 'claude-haiku-4-5-20251001', tier: 'simple', cost: 0.001, policy: 'acme-default' },
  { id: '6', ts: '18m ago', user: 'bob', requested: 'claude-opus-4-6', used: 'claude-sonnet-4-5-20250929', tier: 'medium', cost: 0.024, policy: 'intern-cap' },
];

const TIER_BADGE: Record<string, string> = {
  simple: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  medium: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  complex: 'bg-red-500/10 text-red-400 border-red-500/20',
};

function shortModel(m: string) {
  if (m.includes('haiku')) return 'Haiku';
  if (m.includes('sonnet')) return 'Sonnet';
  if (m.includes('opus')) return 'Opus';
  if (m.includes('gpt-4o-mini')) return 'GPT-4o Mini';
  if (m.includes('gpt-4o')) return 'GPT-4o';
  return m.slice(0, 20);
}

export default function HarnessDashboardPage() {
  const [kpi, setKpi] = useState(DEMO_KPI);
  const [requests, setRequests] = useState(DEMO_REQUESTS);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch('/api/v1/harness/spend').then(r => r.ok ? r.json() : null),
      fetch('/api/v1/harness/spend/breakdown').then(r => r.ok ? r.json() : null),
    ])
      .then(([spend, breakdown]) => {
        if (spend) {
          setKpi(prev => ({
            ...prev,
            requestsToday: spend.daily_requests ?? prev.requestsToday,
            costToday: spend.daily_cost_usd ?? prev.costToday,
          }));
        }
      })
      .catch(() => { /* keep demo data */ })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-[72rem] space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-100">LLM Harness</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Smart proxy routing AI coding tools to the optimal model per request.
        </p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard icon={Activity} label="Requests Today" value={DEMO_KPI.requestsToday.toLocaleString()} />
        <KpiCard icon={DollarSign} label="Cost Today" value={`$${DEMO_KPI.costToday.toFixed(2)}`} />
        <KpiCard
          icon={Zap}
          label="Estimated Savings"
          value={`$${DEMO_KPI.savingsToday.toFixed(2)}`}
          accent="text-emerald-400"
        />
        <KpiCard icon={Gauge} label="Override Rate" value={`${(DEMO_KPI.overrideRate * 100).toFixed(0)}%`} sub="of requests routed to cheaper model" />
      </div>

      {/* Tier Distribution */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
        <h2 className="text-sm font-medium text-zinc-300 mb-4">Requests by Complexity Tier</h2>
        <div className="flex gap-1 h-4 rounded-full overflow-hidden">
          {DEMO_TIER_DIST.map((t) => (
            <div key={t.tier} className={`${t.color} transition-all`} style={{ width: `${t.pct}%` }} />
          ))}
        </div>
        <div className="flex gap-6 mt-3">
          {DEMO_TIER_DIST.map((t) => (
            <div key={t.tier} className="flex items-center gap-2 text-xs text-zinc-400">
              <div className={`w-2 h-2 rounded-full ${t.color}`} />
              <span className="capitalize">{t.tier}</span>
              <span className="text-zinc-500">{t.count} ({t.pct}%)</span>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Requests Table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6">
        <h2 className="text-sm font-medium text-zinc-300 mb-4">Recent Requests</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
                <th className="pb-2 pr-4">Time</th>
                <th className="pb-2 pr-4">User</th>
                <th className="pb-2 pr-4">Requested</th>
                <th className="pb-2 pr-4">Routed To</th>
                <th className="pb-2 pr-4">Tier</th>
                <th className="pb-2 pr-4">Cost</th>
                <th className="pb-2">Policy</th>
              </tr>
            </thead>
            <tbody>
              {DEMO_REQUESTS.map((r) => (
                <tr key={r.id} className="border-b border-zinc-800/50 text-zinc-300">
                  <td className="py-2 pr-4 text-xs text-zinc-500">{r.ts}</td>
                  <td className="py-2 pr-4">{r.user}</td>
                  <td className="py-2 pr-4 text-zinc-500">{shortModel(r.requested)}</td>
                  <td className="py-2 pr-4 font-medium">
                    {r.requested !== r.used && <ArrowDownRight className="inline w-3 h-3 mr-1 text-emerald-400" />}
                    {shortModel(r.used)}
                  </td>
                  <td className="py-2 pr-4">
                    <span className={`inline-block px-2 py-0.5 text-xs rounded-full border ${TIER_BADGE[r.tier] || ''}`}>
                      {r.tier}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-xs">${r.cost.toFixed(4)}</td>
                  <td className="py-2 text-xs text-zinc-500">{r.policy}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
