import Link from 'next/link';
import { Monitor, AlertTriangle } from 'lucide-react';
import { adminApi } from '@/utils/api';
import { StatCard } from '@/components/stat-card';
import { CostTrendChart } from '@/components/cost-trend-chart';
import { ModelUsageChart } from '@/components/model-usage-chart';
import { DashboardHealth } from '@/components/dashboard-health';
import { DashboardClientWidgets } from '@/components/dashboard/client-widgets';
import type { RunSummary } from '@/utils/types';

export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  let agentCount = 0;
  let runCount = 0;
  let sessionCount = 0;
  let totalTokens = 0;
  let totalCost = 0;
  let piiEvents = 0;
  let costByModel: Record<string, number> = {};
  let tokensByModel: Record<string, number> = {};
  let recentRuns: RunSummary[] = [];
  let apiError = false;

  try {
    const [agents, runs, sessions, costs, usage, risks] = await Promise.all([
      adminApi.listAgents(),
      adminApi.listRuns({ limit: 200 }),
      adminApi.listSessions(),
      adminApi.getCosts(),
      adminApi.getUsage(),
      adminApi.getRisks(),
    ]);
    agentCount = agents.length;
    runCount = runs.items.length;
    recentRuns = runs.items;
    sessionCount = sessions.items.length;
    totalTokens = usage.total_tokens;
    totalCost = costs.total_cost_usd;
    piiEvents = risks.pii_events + risks.hallucination_flags;
    costByModel = costs.by_model ?? {};
    tokensByModel = usage.by_model ?? {};
  } catch {
    apiError = true;
  }

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
      <div className="flex items-center justify-between mt-0 mb-lg">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">Dashboard</h1>
        <a
          href="/tv"
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border-dark text-text-secondary hover:bg-surface-dark-hover hover:text-text-on-dark transition-colors"
        >
          <Monitor className="w-3.5 h-3.5" /> TV Mode
        </a>
      </div>

      {/* API error banner */}
      {apiError && (
        <div className="flex items-center gap-3 bg-error/5 border border-error/20 rounded-lg px-4 py-3 mb-lg">
          <AlertTriangle className="w-4 h-4 text-error flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-error m-0">Unable to load dashboard data</p>
            <p className="text-xs text-text-muted m-0 mt-0.5">
              The API server is not responding. Start the backend with{' '}
              <code className="font-[family-name:var(--font-mono)] px-1 rounded">make dev-native APP=admin</code>{' '}
              and refresh the page.
            </p>
          </div>
        </div>
      )}

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

      {/* Health widget + Quick actions (client-side) */}
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
        <div className="bg-surface-dark rounded-lg border border-border-dark p-lg mb-lg">
          <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
            Top Cost Models
          </h3>
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border-dark">
                <th className="text-left py-2 px-3 text-xs text-text-muted uppercase tracking-wide">Model</th>
                <th className="text-right py-2 px-3 text-xs text-text-muted uppercase tracking-wide">Cost</th>
              </tr>
            </thead>
            <tbody>
              {costModelEntries.map(([model, cost]) => (
                <tr key={model} className="border-b border-border-dark last:border-0 hover:bg-surface-dark-hover transition-colors">
                  <td className="py-2 px-3">{model}</td>
                  <td className="py-2 px-3 text-right text-primary">${cost.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Utility Actions */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-md mb-lg">
        {[
          { label: 'Run Evaluation', href: '/eval/run', description: 'Test agent quality' },
          { label: 'Create Token', href: '/settings/tokens', description: 'Generate API tokens' },
          { label: 'View Audit Log', href: '/safety/audit', description: 'Review safety events' },
          { label: 'System Health', href: '/settings/health', description: 'Check infrastructure' },
        ].map((action) => (
          <Link
            key={action.href}
            href={action.href}
            className="bg-surface-dark rounded-lg border border-border-dark p-md no-underline text-inherit hover:border-primary/40 hover:bg-surface-dark-hover transition-colors"
          >
            <div className="text-sm font-semibold text-primary">{action.label}</div>
            <div className="text-xs text-text-muted mt-1">{action.description}</div>
          </Link>
        ))}
      </div>

      {/* Recent Activity */}
      {recentRuns.length > 0 && (
        <div className="bg-surface-dark rounded-lg border border-border-dark p-lg">
          <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Recent Activity</h3>
          <div className="flex flex-col">
            {recentRuns.slice(0, 5).map((run) => (
              <div key={run.run_id} className="flex justify-between items-center py-2 border-b border-border-dark last:border-0 hover:bg-surface-dark-hover transition-colors px-1 rounded">
                <div>
                  <span className="font-medium text-sm">{run.agent_name}</span>
                  <span className="text-text-muted text-xs ml-2">
                    {run.input_preview?.slice(0, 50) || '\u2014'}
                  </span>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${
                  run.status === 'completed'
                    ? 'bg-success/15 text-success'
                    : 'bg-white/5 text-text-secondary'
                }`}>
                  {run.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
