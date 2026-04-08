'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import { playgroundApi } from '@/utils/playground-api';
import { StatCard } from '@/components/stat-card';
import { ModelComparisonTable } from '@/components/model-comparison-table';
import { Skeleton } from '@sagecurator/ui';

const LOCAL_PROVIDERS = new Set(['ollama', 'lmstudio']);

export default function ModelComparisonPage() {
  const [loading, setLoading] = useState(true);
  const [modelData, setModelData] = useState<{
    model: string;
    total_cost_usd: number;
    total_tokens: number;
    request_count: number;
    cost_per_1k_tokens: number;
    is_local?: boolean;
  }[]>([]);

  useEffect(() => {
    async function fetchData() {
      try {
        const [models, availableModels] = await Promise.all([
          adminApi.getModelAnalytics(),
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

        setModelData(models.map((m: any) => ({
          ...m,
          is_local: m.is_local || localModelIds.has(m.model),
        })));
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
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Model Comparison</h1>
        <Skeleton lines={8} />
      </div>
    );
  }

  const commercial = modelData.filter((m) => !m.is_local);
  const local = modelData.filter((m) => m.is_local);

  const totalRequests = modelData.reduce((sum, m) => sum + (m.request_count ?? 0), 0);
  const cheapestCommercial = commercial.length > 0
    ? [...commercial].sort((a, b) => (a.cost_per_1k_tokens ?? 0) - (b.cost_per_1k_tokens ?? 0))[0]
    : null;
  const mostUsedModel = modelData.length > 0
    ? [...modelData].sort((a, b) => (b.request_count ?? 0) - (a.request_count ?? 0))[0]
    : null;

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Model Comparison</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Side-by-side comparison of all LLM models used across agents.
      </p>

      {/* Summary cards */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-md mb-lg">
        <StatCard label="Commercial Models" value={commercial.length} />
        <StatCard label="Local Models" value={local.length} />
        <StatCard label="Total Requests" value={totalRequests.toLocaleString()} />
        <StatCard
          label="Cheapest Commercial"
          value={cheapestCommercial ? cheapestCommercial.model : '--'}
        />
        <StatCard
          label="Most Used Model"
          value={mostUsedModel ? mostUsedModel.model : '--'}
        />
      </div>

      {/* Comparison table */}
      <ModelComparisonTable data={modelData} />
    </div>
  );
}
