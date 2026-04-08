'use client';

import { Card } from '@sagecurator/ui';
import { FileText, Layers, Target, Database } from 'lucide-react';
import type { ContextStats, ContextScopeInfo } from '@/utils/types';

interface Props {
  stats: ContextStats | null;
  scopes: ContextScopeInfo[];
  loading?: boolean;
}

export function ContextStatsGrid({ stats, scopes, loading }: Props) {
  if (loading || !stats) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-md">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} className="p-md animate-pulse">
            <div className="h-4 bg-white/10 rounded w-20 mb-2" />
            <div className="h-8 bg-white/10 rounded w-16" />
          </Card>
        ))}
      </div>
    );
  }

  const cards = [
    { label: 'Documents', value: stats.documents, icon: FileText, color: 'text-blue-400' },
    { label: 'Chunks', value: stats.chunks, icon: Layers, color: 'text-teal-400' },
    {
      label: 'Active Scopes',
      value: scopes.filter((s) => s.document_count > 0).length,
      icon: Target,
      color: 'text-purple-400',
    },
    {
      label: 'Status',
      value: stats.status === 'active' ? 'Active' : 'Not Configured',
      icon: Database,
      color: stats.status === 'active' ? 'text-green-400' : 'text-yellow-400',
    },
  ];

  return (
    <div className="space-y-lg">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-md">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <Card key={c.label} className="p-md">
              <div className="flex items-center gap-2 text-text-muted text-xs mb-1">
                <Icon size={14} className={c.color} />
                {c.label}
              </div>
              <div className="text-2xl font-bold font-[family-name:var(--font-heading)]">
                {c.value}
              </div>
            </Card>
          );
        })}
      </div>

      {/* Scope breakdown */}
      {scopes.length > 0 && (
        <Card className="p-md">
          <h3 className="text-sm font-semibold mb-md">Knowledge by Scope</h3>
          <div className="space-y-3">
            {scopes.map((s) => {
              const maxDocs = Math.max(...scopes.map((x) => x.document_count), 1);
              const pct = Math.round((s.document_count / maxDocs) * 100);
              return (
                <div key={s.scope}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="capitalize font-medium">{s.scope}</span>
                    <span className="text-text-muted">
                      {s.document_count} docs / {s.chunk_count} chunks
                    </span>
                  </div>
                  <div className="h-2 bg-white/5 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Source breakdown */}
      {Object.keys(stats.by_source).length > 0 && (
        <Card className="p-md">
          <h3 className="text-sm font-semibold mb-md">Documents by Source</h3>
          <div className="flex flex-wrap gap-3">
            {Object.entries(stats.by_source).map(([source, count]) => (
              <div key={source} className="flex items-center gap-2 text-xs">
                <span className="capitalize font-medium">{source}</span>
                <span className="bg-white/10 px-2 py-0.5 rounded text-text-muted">{count}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
