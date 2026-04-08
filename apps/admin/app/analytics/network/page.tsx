'use client';

import { useEffect, useState } from 'react';
import { Card } from '@sagecurator/ui';
import { useLicense } from '@/utils/license';
import { PremiumUpgradeCTA } from '@/components/premium-upgrade-cta';
import { adminApi } from '@/utils/api';
import { NetworkControls } from '@/components/premium/network-controls';
import dynamic from 'next/dynamic';

const AgentNetwork = dynamic(
  () => import('@/components/premium/agent-network').then((m) => m.AgentNetwork),
  { loading: () => <p className="text-sm text-text-muted p-md">Loading network graph...</p> },
);

interface NodeData {
  id: string;
  tokens: number;
  runs: number;
  error_rate: number;
}

interface EdgeData {
  source: string;
  target: string;
  weight: number;
}

export default function AgentNetworkPage() {
  const license = useLicense();
  const [nodes, setNodes] = useState<NodeData[]>([]);
  const [edges, setEdges] = useState<EdgeData[]>([]);
  const [loading, setLoading] = useState(true);

  // Controls state
  const today = new Date().toISOString().slice(0, 10);
  const thirtyDaysAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const [fromDate, setFromDate] = useState(thirtyDaysAgo);
  const [toDate, setToDate] = useState(today);
  const [layoutMode, setLayoutMode] = useState<'force' | 'hierarchical' | 'circular'>('force');
  const [filter, setFilter] = useState('');

  useEffect(() => {
    if (!license.isPremium && !license.loading) {
      setLoading(false);
      return;
    }

    async function load() {
      try {
        const data = await adminApi.getAgentNetwork(fromDate, toDate);
        setNodes(data.nodes);
        setEdges(data.edges);
      } catch {
        // fallback to empty
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [fromDate, toDate, license.isPremium, license.loading]);

  // Filter nodes by name if filter is set
  const filteredNodes = filter
    ? nodes.filter((n) => n.id.toLowerCase().includes(filter.toLowerCase()))
    : nodes;
  const filteredNodeIds = new Set(filteredNodes.map((n) => n.id));
  const filteredEdges = edges.filter(
    (e) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target),
  );

  if (!license.isPremium && !license.loading) {
    return (
      <div className="max-w-5xl mx-auto">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-lg">
          Agent Network
        </h1>
        <PremiumUpgradeCTA feature="Agent Interaction Network" />
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-md">
        Agent Network
      </h1>
      <p className="text-sm text-text-muted mb-lg">
        Visualize how agents interact across workflows. Node size reflects token usage,
        color indicates error rate, and edge thickness shows interaction frequency.
      </p>

      <NetworkControls
        fromDate={fromDate}
        toDate={toDate}
        onFromChange={setFromDate}
        onToChange={setToDate}
        layoutMode={layoutMode}
        onLayoutChange={setLayoutMode}
        filter={filter}
        onFilterChange={setFilter}
      />

      {loading ? (
        <Card className="!p-8">
          <div className="text-sm text-text-muted text-center">Loading agent network...</div>
        </Card>
      ) : (
        <AgentNetwork nodes={filteredNodes} edges={filteredEdges} />
      )}

      {/* Legend */}
      <div className="flex items-center gap-6 mt-4 text-[10px] text-text-muted">
        <span className="font-semibold uppercase">Error Rate:</span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-full inline-block" style={{ background: '#22c55e' }} />
          0%
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-full inline-block" style={{ background: '#eab308' }} />
          5%
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-full inline-block" style={{ background: '#ef4444' }} />
          &gt;10%
        </span>
        <span className="ml-4 font-semibold uppercase">Node size = token volume</span>
      </div>
    </div>
  );
}
