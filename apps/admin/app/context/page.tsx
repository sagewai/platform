'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card } from '@/components/ui/legacy';
import { FileText, Search, Settings2, Sparkles } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { ContextStats, ContextScopeInfo } from '@/utils/types';
import { ContextStatsGrid } from '@/components/context-stats-grid';

export default function ContextDashboardPage() {
  const [stats, setStats] = useState<ContextStats | null>(null);
  const [scopes, setScopes] = useState<ContextScopeInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [statsData, scopesData] = await Promise.all([
          adminApi.getContextStats(),
          adminApi.getContextScopes(),
        ]);
        setStats(statsData);
        setScopes(scopesData.scopes);
      } catch {
        // leave defaults
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const actions = [
    {
      href: '/context/documents',
      icon: FileText,
      label: 'Documents',
      description: 'Upload, ingest, and manage knowledge documents across all scopes',
      color: 'text-blue-400',
    },
    {
      href: '/context/search',
      icon: Search,
      label: 'Search',
      description: 'Multi-strategy semantic search across vector, BM25, and graph indexes',
      color: 'text-teal-400',
    },
    {
      href: '/context/lifecycle',
      icon: Settings2,
      label: 'Lifecycle',
      description: 'Run maintenance, resolve conflicts, and manage knowledge freshness',
      color: 'text-amber-400',
    },
    {
      href: '/context/directives',
      icon: Sparkles,
      label: 'Directives',
      description: 'Configure prompt directives and browse auto-extracted memories',
      color: 'text-purple-400',
    },
  ];

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-xl">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1">Context Engine</h1>
        <p className="text-text-muted text-sm max-w-[42rem]">
          Enterprise-grade knowledge management for your AI agents. Ingest documents, configure scoped
          retrieval, and let your agents access the right knowledge at the right time — regardless of which LLM they use.
        </p>
      </div>

      <ContextStatsGrid stats={stats} scopes={scopes} loading={loading} />

      <div className="mt-xl">
        <h2 className="text-sm font-semibold text-text-muted mb-md uppercase tracking-wide">Quick Actions</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-md">
          {actions.map((action) => {
            const Icon = action.icon;
            return (
              <Link key={action.href} href={action.href} className="no-underline">
                <Card className="p-md hover:bg-primary/5 dark:hover:bg-white/[0.03] transition-colors h-full">
                  <Icon size={20} className={`${action.color} mb-2`} />
                  <div className="font-medium text-sm mb-1">{action.label}</div>
                  <div className="text-xs text-text-muted leading-relaxed">{action.description}</div>
                </Card>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
