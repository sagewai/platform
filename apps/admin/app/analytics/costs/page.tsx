'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import { playgroundApi } from '@/utils/playground-api';
import { StatCard } from '@/components/stat-card';
import { CostByModelChart } from '@/components/cost-by-model-chart';
import { CostBreakdownTable } from '@/components/cost-breakdown-table';
import { Badge, Skeleton } from '@sagecurator/ui';

const LOCAL_PROVIDERS = new Set(['ollama', 'lmstudio']);

export default function CostAnalyticsPage() {
  const [loading, setLoading] = useState(true);
  const [totalCost, setTotalCost] = useState(0);
  const [recordCount, setRecordCount] = useState(0);
  const [costByModel, setCostByModel] = useState<Record<string, number>>({});
  const [costByAgent, setCostByAgent] = useState<Record<string, number>>({});
  const [modelData, setModelData] = useState<{
    model: string;
    total_cost_usd: number;
    total_tokens: number;
    request_count: number;
    cost_per_1k_tokens: number;
    is_local?: boolean;
  }[]>([]);
  const [agentData, setAgentData] = useState<{
    agent_name: string;
    total_cost_usd: number;
    total_tokens: number;
    request_count: number;
    models_used: string[];
  }[]>([]);
  const [budgetLimits, setBudgetLimits] = useState<{
    agent_name: string;
    max_daily_usd: number;
    max_monthly_usd: number;
    action: string;
  }[]>([]);

  useEffect(() => {
    async function fetchData() {
      try {
        const [costs, models, agents, limits, availableModels] = await Promise.all([
          adminApi.getCosts(),
          adminApi.getModelAnalytics(),
          adminApi.getAgentAnalytics(),
          adminApi.listBudgetLimits(),
          playgroundApi.listModels().catch(() => [] as any[]),
        ]);

        // Build set of local model IDs from the model router (authoritative source)
        const localModelIds = new Set<string>();
        for (const m of availableModels) {
          const id = typeof m === 'string' ? m : m.id;
          const provider = typeof m === 'string' ? '' : (m.provider ?? '');
          if (LOCAL_PROVIDERS.has(provider)) {
            localModelIds.add(id);
          }
        }

        // Enrich model analytics with local detection from model router
        const enrichedModels = models.map((m: any) => ({
          ...m,
          is_local: m.is_local || localModelIds.has(m.model),
        }));

        setTotalCost(costs.total_cost_usd ?? 0);
        setRecordCount(costs.record_count ?? 0);
        setCostByModel(costs.by_model || {});
        setCostByAgent(costs.by_agent || {});
        setModelData(enrichedModels);
        setAgentData(agents);
        setBudgetLimits(limits);
      } catch {
        // API unavailable
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto">
        <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Cost Analytics</h1>
        <Skeleton lines={8} />
      </div>
    );
  }

  // Split models into commercial vs local
  const commercialModels = modelData.filter((m) => !m.is_local);
  const localModels = modelData.filter((m) => m.is_local);
  const localModelNames = new Set(localModels.map((m) => m.model));

  // Chart data — only commercial models have real costs
  const modelChartData = Object.entries(costByModel)
    .filter(([model]) => !localModelNames.has(model))
    .map(([model, cost]) => ({ model, cost }))
    .sort((a, b) => b.cost - a.cost);

  // Commercial cost only (exclude local)
  const commercialCost = commercialModels.reduce((sum, m) => sum + (m.total_cost_usd ?? 0), 0);
  const localTokens = localModels.reduce((sum, m) => sum + (m.total_tokens ?? 0), 0);
  const localRequests = localModels.reduce((sum, m) => sum + (m.request_count ?? 0), 0);

  // Build breakdown tables
  const commercialTableData = commercialModels.map((m) => ({
    name: m.model,
    cost: m.total_cost_usd ?? 0,
    tokens: m.total_tokens ?? 0,
    requests: m.request_count ?? 0,
  }));

  const localTableData = localModels.map((m) => ({
    name: m.model,
    cost: 0,
    tokens: m.total_tokens ?? 0,
    requests: m.request_count ?? 0,
  }));

  const agentTableData = agentData.map((a) => ({
    name: a.agent_name,
    cost: a.total_cost_usd ?? 0,
    tokens: a.total_tokens ?? 0,
    requests: a.request_count ?? 0,
  }));

  // Budget alert for agents over threshold
  const budgetAlerts = budgetLimits.filter((limit) => {
    const agentCost = costByAgent[limit.agent_name] ?? 0;
    return agentCost > limit.max_daily_usd * 0.8;
  });

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Cost Analytics</h1>

      {/* Budget alerts */}
      {budgetAlerts.length > 0 && (
        <div className="bg-warning-light border border-warning/30 rounded-lg px-5 py-3 mb-lg">
          <strong className="text-warning-dark">Budget Alerts</strong>
          {budgetAlerts.map((alert) => (
            <p key={alert.agent_name} className="mt-1 mb-0 text-sm text-warning-dark">
              {alert.agent_name}: approaching daily limit of ${alert.max_daily_usd.toFixed(2)} (
              action: {alert.action})
            </p>
          ))}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-md mb-lg">
        <StatCard label="Commercial Cost" value={`$${commercialCost.toFixed(4)}`} />
        <StatCard label="API Calls" value={recordCount} />
        <StatCard label="Commercial Models" value={commercialModels.length} />
        <StatCard label="Local Models" value={localModels.length} />
        {localModels.length > 0 && (
          <StatCard label="Local Tokens (Free)" value={localTokens.toLocaleString()} />
        )}
      </div>

      {/* Cost by model chart — commercial only */}
      {modelChartData.length > 0 && (
        <div className="mb-lg">
          <CostByModelChart data={modelChartData} />
        </div>
      )}

      {/* Commercial models breakdown */}
      {commercialModels.length > 0 && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(400px,1fr))] gap-md mb-lg">
          <CostBreakdownTable title="Commercial Models" data={commercialTableData} />
          <CostBreakdownTable title="Cost by Agent" data={agentTableData} />
        </div>
      )}

      {/* If only local models exist, still show agent breakdown */}
      {commercialModels.length === 0 && agentTableData.length > 0 && (
        <div className="mb-lg">
          <CostBreakdownTable title="Cost by Agent" data={agentTableData} />
        </div>
      )}

      {/* Local models section */}
      {localModels.length > 0 && (
        <div className="bg-success-light/30 border border-success/20 rounded-lg p-5">
          <div className="flex items-center gap-2 mb-3">
            <h3 className="m-0 text-base font-semibold font-[family-name:var(--font-heading)]">Local Models</h3>
            <Badge variant="success">Free</Badge>
          </div>
          <p className="mt-0 mb-3 text-sm text-text-secondary">
            These models run locally (Ollama, LM Studio) and incur no API costs.
          </p>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-md mb-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-success">{localModels.length}</div>
              <div className="text-xs text-text-muted">Models</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-success">{localRequests.toLocaleString()}</div>
              <div className="text-xs text-text-muted">Requests</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-success">{localTokens.toLocaleString()}</div>
              <div className="text-xs text-text-muted">Tokens</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-success">$0.00</div>
              <div className="text-xs text-text-muted">Total Cost</div>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-success/20">
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Model</th>
                  <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Cost</th>
                  <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Tokens</th>
                  <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Requests</th>
                </tr>
              </thead>
              <tbody>
                {localTableData.map((entry) => (
                  <tr key={entry.name} className="border-b border-success/10 last:border-0">
                    <td className="py-2.5 px-3 font-medium">{entry.name}</td>
                    <td className="py-2.5 px-3 text-right text-success font-medium">$0.0000</td>
                    <td className="py-2.5 px-3 text-right">{entry.tokens.toLocaleString()}</td>
                    <td className="py-2.5 px-3 text-right">{entry.requests.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
