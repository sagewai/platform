'use client';

// The dashboard fetches authenticated control-plane data, so it must run in the
// BROWSER, not as a Server Component. Server-side rendering happens inside the
// admin container, where (a) `localhost:8000` is the admin itself rather than the
// backend, and (b) there is no access to the user's bearer token (it lives in
// browser storage) — so the calls fail/401 and the page falls to "Backend not
// reachable". Fetching client-side reuses the same auth + host the other admin
// pages use. See utils/connection.tsx for the matching client-side health check.

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Monitor, ArrowUpRight, Sparkles } from 'lucide-react';
import { adminApi } from '@/utils/api';
import { StatCard } from '@/components/stat-card';
import { CostTrendChart } from '@/components/cost-trend-chart';
import { ModelUsageChart } from '@/components/model-usage-chart';
import { DashboardHealth } from '@/components/dashboard-health';
import { DashboardClientWidgets } from '@/components/dashboard/client-widgets';
import { SystemOffline } from '@/components/system-offline';
import { EmptyState } from '@/components/ui/empty-state';
import { buttonVariants } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ProjectBadge } from '@/components/project-badge';
import type { RunSummary } from '@/utils/types';

type LoadState = 'loading' | 'error' | 'ready';

export default function DashboardPage() {
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [agentCount, setAgentCount] = useState(0);
  const [runCount, setRunCount] = useState(0);
  const [sessionCount, setSessionCount] = useState(0);
  const [totalTokens, setTotalTokens] = useState(0);
  const [totalCost, setTotalCost] = useState(0);
  const [piiEvents, setPiiEvents] = useState(0);
  const [costByModel, setCostByModel] = useState<Record<string, number>>({});
  const [tokensByModel, setTokensByModel] = useState<Record<string, number>>({});
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);

  const load = useCallback(async () => {
    setLoadState('loading');
    try {
      const [agents, runs, sessions, costs, usage, risks] = await Promise.all([
        adminApi.listAgents(),
        adminApi.listRuns({ limit: 200 }),
        adminApi.listSessions(),
        adminApi.getCosts(),
        adminApi.getUsage(),
        adminApi.getRisks(),
      ]);
      setAgentCount(agents.length);
      setRunCount(runs.items.length);
      setRecentRuns(runs.items);
      setSessionCount(sessions.items.length);
      setTotalTokens(usage.total_tokens);
      setTotalCost(costs.total_cost_usd);
      setPiiEvents(risks.pii_events + risks.hallucination_flags);
      setCostByModel(costs.by_model ?? {});
      setTokensByModel(usage.by_model ?? {});
      setLoadState('ready');
    } catch {
      setLoadState('error');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // ── Header (always visible) ──
  const header = (
    <div className="flex items-center justify-between mt-0 mb-lg">
      <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] flex items-center">Dashboard <ProjectBadge /></h1>
      <a href="/tv" className={buttonVariants({ variant: 'outline', size: 'sm' })}>
        <Monitor className="mr-1.5 h-3.5 w-3.5" /> TV Mode
      </a>
    </div>
  );

  // ── Loading: show the shell while the first fetch lands ──
  if (loadState === 'loading') {
    return (
      <div className="max-w-6xl mx-auto">
        {header}
        <div className="mt-8 text-sm text-muted-foreground">Loading dashboard…</div>
      </div>
    );
  }

  // ── API down: friendly empty state instead of red banner ──
  if (loadState === 'error') {
    return (
      <div className="max-w-6xl mx-auto">
        {header}
        <div className="mt-8">
          <SystemOffline onRetry={load} />
        </div>
      </div>
    );
  }

  // ── Fresh install: no activity yet ──
  const hasAnyActivity = agentCount > 0 || runCount > 0 || sessionCount > 0 || totalTokens > 0;
  if (!hasAnyActivity) {
    return (
      <div className="max-w-6xl mx-auto">
        {header}
        <DashboardClientWidgets />
        <div className="mt-8">
          <EmptyState
            icon={Sparkles}
            title="No activity yet"
            description="Spin up your first agent and run it from the playground to see metrics here."
            action={
              <Link href="/playground" className={buttonVariants()}>
                Try the playground <ArrowUpRight className="ml-1.5 h-4 w-4" />
              </Link>
            }
          />
        </div>
      </div>
    );
  }

  // ── Normal dashboard render ──
  const today = new Date().toISOString().slice(0, 10);
  const costTrendData = totalCost > 0 ? [{ date: today, cost: totalCost }] : [];
  const modelUsageData = Object.entries(tokensByModel).map(([model, tokens]) => ({
    model,
    tokens,
  }));
  const costModelEntries = Object.entries(costByModel)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5);

  return (
    <div className="max-w-6xl mx-auto">
      {header}

      {/* Role-based welcome, getting started, and quick actions */}
      <DashboardClientWidgets />

      {/* KPI cards */}
      <div data-tour="dashboard-kpis" className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-md mb-lg">
        <StatCard label="Registered Agents" value={agentCount} />
        <StatCard label="Total Runs" value={runCount} />
        <StatCard label="Active Sessions" value={sessionCount} />
        <StatCard label="Total Tokens" value={totalTokens.toLocaleString()} />
        <StatCard label="Total Cost" value={`$${totalCost.toFixed(4)}`} />
        <StatCard label="Risk Events" value={piiEvents} />
      </div>

      {/* Health widget */}
      <div className="space-y-md mb-lg">
        <DashboardHealth />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-md mb-lg">
        <CostTrendChart data={costTrendData} />
        <ModelUsageChart data={modelUsageData} />
      </div>

      {/* Top cost models */}
      {costModelEntries.length > 0 && (
        <Card className="mb-lg">
          <CardHeader>
            <CardTitle className="text-base font-semibold">Top Cost Models</CardTitle>
          </CardHeader>
          <CardContent>
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 px-3 text-xs text-muted-foreground uppercase tracking-wide">Model</th>
                  <th className="text-right py-2 px-3 text-xs text-muted-foreground uppercase tracking-wide">Cost</th>
                </tr>
              </thead>
              <tbody>
                {costModelEntries.map(([model, cost]) => (
                  <tr key={model} className="border-b border-border last:border-0 hover:bg-accent/40 transition-colors">
                    <td className="py-2 px-3">{model}</td>
                    <td className="py-2 px-3 text-right text-primary">${cost.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {/* Utility Actions */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-md mb-lg">
        {[
          { label: 'Run Evaluation', href: '/eval/run', description: 'Test agent quality' },
          { label: 'Create Token', href: '/account/tokens', description: 'Generate API tokens' },
          { label: 'View Audit Log', href: '/safety/audit', description: 'Review safety events' },
          { label: 'System Health', href: '/system/health', description: 'Check infrastructure' },
        ].map((action) => (
          <Link
            key={action.href}
            href={action.href}
            className="rounded-lg border border-border bg-card text-card-foreground p-md no-underline hover:border-primary/40 hover:bg-accent/40 transition-colors"
          >
            <div className="text-sm font-semibold text-primary">{action.label}</div>
            <div className="text-xs text-muted-foreground mt-1">{action.description}</div>
          </Link>
        ))}
      </div>

      {/* Recent Activity */}
      {recentRuns.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-semibold">Recent Activity</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col">
              {recentRuns.slice(0, 5).map((run) => (
                <div
                  key={run.run_id}
                  className="flex justify-between items-center py-2 border-b border-border last:border-0 hover:bg-accent/40 transition-colors px-1 rounded"
                >
                  <div>
                    <span className="font-medium text-sm">{run.agent_name}</span>
                    <span className="text-muted-foreground text-xs ml-2">
                      {run.input_preview?.slice(0, 50) || '—'}
                    </span>
                  </div>
                  <Badge variant={run.status === 'completed' ? 'default' : 'secondary'}>
                    {run.status}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
