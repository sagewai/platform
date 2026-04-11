'use client';

import { useState } from 'react';
import { Card, Badge, EmptyState } from '@/components/ui/legacy';

interface ModelMetrics {
  model: string;
  provider?: string;
  total_cost_usd: number;
  total_tokens: number;
  request_count: number;
  cost_per_1k_tokens: number;
  is_local?: boolean;
  avg_latency_ms?: number;
  success_rate?: number;
}

interface ModelComparisonTableProps {
  data: ModelMetrics[];
}

type SortKey = keyof ModelMetrics;

/** Infer provider from model name. */
function inferProvider(model: string): string {
  const lower = model.toLowerCase();
  if (lower.startsWith('ollama/')) return 'Ollama (Local)';
  if (lower.includes('lmstudio') || lower.includes('lm-studio')) return 'LM Studio (Local)';
  if (lower.includes('gpt') || lower.includes('o1') || lower.includes('o3')) return 'OpenAI';
  if (lower.includes('claude')) return 'Anthropic';
  if (lower.includes('gemini')) return 'Google';
  if (lower.includes('mistral') || lower.includes('mixtral')) return 'Mistral';
  if (lower.includes('llama') || lower.includes('meta')) return 'Meta';
  if (lower.includes('deepseek')) return 'DeepSeek';
  if (lower.includes('command') || lower.includes('cohere')) return 'Cohere';
  return 'Other';
}

function isLocalModel(model: string): boolean {
  const lower = model.toLowerCase();
  return lower.startsWith('ollama/') || lower.includes('lmstudio') || lower.includes('lm-studio');
}

export function ModelComparisonTable({ data }: ModelComparisonTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('total_cost_usd');
  const [sortDesc, setSortDesc] = useState(true);

  // Enrich data with provider + safe defaults
  const enriched: ModelMetrics[] = data.map((m) => ({
    ...m,
    request_count: m.request_count ?? 0,
    cost_per_1k_tokens: m.cost_per_1k_tokens ?? (m.total_tokens > 0 ? m.total_cost_usd / m.total_tokens * 1000 : 0),
    is_local: m.is_local ?? isLocalModel(m.model),
    provider: inferProvider(m.model),
    avg_latency_ms: m.avg_latency_ms ?? 0,
    success_rate: m.success_rate ?? 100,
  }));

  const commercial = enriched.filter((m) => !m.is_local);
  const local = enriched.filter((m) => m.is_local);

  function sortModels(models: ModelMetrics[]) {
    return [...models].sort((a, b) => {
      const av = a[sortKey] ?? 0;
      const bv = b[sortKey] ?? 0;
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortDesc ? bv.localeCompare(av) : av.localeCompare(bv);
      }
      return sortDesc ? (bv as number) - (av as number) : (av as number) - (bv as number);
    });
  }

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDesc(!sortDesc);
    } else {
      setSortKey(key);
      setSortDesc(true);
    }
  }

  const indicator = (key: SortKey) => (sortKey === key ? (sortDesc ? ' v' : ' ^') : '');

  const thClass = (key: SortKey, align: string = 'left') =>
    `text-${align} py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide cursor-pointer select-none whitespace-nowrap ${sortKey === key ? 'bg-primary-light' : ''}`;

  if (data.length === 0) {
    return (
      <Card>
        <EmptyState title="No Data" description="No model data available yet." />
      </Card>
    );
  }

  const headerRow = (
    <tr className="border-b-2 border-border">
      <th className={thClass('model')} onClick={() => handleSort('model')}>
        Model{indicator('model')}
      </th>
      <th className={thClass('provider')} onClick={() => handleSort('provider')}>
        Provider{indicator('provider')}
      </th>
      <th className={thClass('request_count', 'right')} onClick={() => handleSort('request_count')}>
        Requests{indicator('request_count')}
      </th>
      <th className={thClass('total_tokens', 'right')} onClick={() => handleSort('total_tokens')}>
        Total Tokens{indicator('total_tokens')}
      </th>
      <th className={thClass('total_cost_usd', 'right')} onClick={() => handleSort('total_cost_usd')}>
        Total Cost{indicator('total_cost_usd')}
      </th>
      <th className={thClass('cost_per_1k_tokens', 'right')} onClick={() => handleSort('cost_per_1k_tokens')}>
        $/1K Tokens{indicator('cost_per_1k_tokens')}
      </th>
      <th className={thClass('avg_latency_ms', 'right')} onClick={() => handleSort('avg_latency_ms')}>
        Avg Latency{indicator('avg_latency_ms')}
      </th>
      <th className={thClass('success_rate', 'right')} onClick={() => handleSort('success_rate')}>
        Success Rate{indicator('success_rate')}
      </th>
    </tr>
  );

  const renderRow = (m: ModelMetrics) => (
    <tr
      key={m.model}
      className={`border-b border-border last:border-0 hover:bg-bg-subtle transition-colors ${m.is_local ? 'bg-success-light/20' : ''}`}
    >
      <td className="py-2.5 px-3 font-medium">
        {m.model}
        {m.is_local && <Badge variant="success" className="ml-2 text-[10px]">Free</Badge>}
      </td>
      <td className="py-2.5 px-3">
        <Badge variant={m.is_local ? 'success' : 'default'}>{m.provider}</Badge>
      </td>
      <td className="py-2.5 px-3 text-right">{m.request_count.toLocaleString()}</td>
      <td className="py-2.5 px-3 text-right">{m.total_tokens.toLocaleString()}</td>
      <td className={`py-2.5 px-3 text-right ${m.is_local ? 'text-success font-medium' : ''}`}>
        ${m.total_cost_usd.toFixed(4)}
      </td>
      <td className={`py-2.5 px-3 text-right ${m.is_local ? 'text-success font-medium' : ''}`}>
        ${m.cost_per_1k_tokens.toFixed(4)}
      </td>
      <td className="py-2.5 px-3 text-right">
        {m.avg_latency_ms ? `${m.avg_latency_ms.toFixed(0)}ms` : '--'}
      </td>
      <td className="py-2.5 px-3 text-right">
        <span className={`font-medium ${(m.success_rate ?? 100) >= 95 ? 'text-success' : 'text-error'}`}>
          {m.success_rate?.toFixed(1) ?? '100.0'}%
        </span>
      </td>
    </tr>
  );

  return (
    <div className="flex flex-col gap-md">
      {/* Commercial models */}
      {commercial.length > 0 && (
        <Card className="overflow-x-auto">
          <h3 className="mt-0 mb-3 text-base font-semibold font-[family-name:var(--font-heading)]">
            Commercial Models
          </h3>
          <table className="w-full text-sm border-collapse min-w-[700px]">
            <thead>{headerRow}</thead>
            <tbody>{sortModels(commercial).map(renderRow)}</tbody>
          </table>
        </Card>
      )}

      {/* Local models */}
      {local.length > 0 && (
        <Card className="overflow-x-auto border-success/30 bg-success-light/10">
          <div className="flex items-center gap-2 mb-3">
            <h3 className="m-0 text-base font-semibold font-[family-name:var(--font-heading)]">
              Local Models
            </h3>
            <Badge variant="success">Free</Badge>
          </div>
          <p className="mt-0 mb-3 text-xs text-text-secondary">
            Running locally via Ollama or LM Studio — no API costs.
          </p>
          <table className="w-full text-sm border-collapse min-w-[700px]">
            <thead>{headerRow}</thead>
            <tbody>{sortModels(local).map(renderRow)}</tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
