'use client';

import { useEffect, useState } from 'react';
import { Card, Button, Skeleton, EmptyState } from '@sagecurator/ui';
import { Download, Filter, Search } from 'lucide-react';

interface RunLog {
  id: string;
  agent_name: string;
  model: string;
  status: 'success' | 'error' | 'timeout';
  tokens: number;
  cost_usd: number;
  duration_ms: number;
  created_at: string;
  messages: number;
}

export default function TrainingLogsPage() {
  const [logs, setLogs] = useState<RunLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    // TODO: Replace with actual API call
    setTimeout(() => {
      setLogs([]);
      setLoading(false);
    }, 500);
  }, []);

  const filtered = logs.filter((l) =>
    l.agent_name.toLowerCase().includes(search.toLowerCase()) ||
    l.model.toLowerCase().includes(search.toLowerCase())
  );

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function selectAll() {
    if (selected.size === filtered.length) setSelected(new Set());
    else setSelected(new Set(filtered.map((l) => l.id)));
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="flex-1 relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            placeholder="Search by agent or model..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-white/5 border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm"
          />
        </div>
        <Button>
          <Filter size={14} className="mr-1.5" />
          Filters
        </Button>
        <Button disabled={selected.size === 0}>
          <Download size={14} className="mr-1.5" />
          Export ({selected.size})
        </Button>
      </div>

      {/* Table */}
      <Card>
        {loading ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No run logs yet"
            description="Agent conversations will appear here once agents start running. Run an agent in the Playground to get started."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="text-left p-3 font-medium">
                    <input type="checkbox" checked={selected.size === filtered.length && filtered.length > 0} onChange={selectAll} />
                  </th>
                  <th className="text-left p-3 font-medium">Agent</th>
                  <th className="text-left p-3 font-medium">Model</th>
                  <th className="text-left p-3 font-medium">Status</th>
                  <th className="text-right p-3 font-medium">Messages</th>
                  <th className="text-right p-3 font-medium">Tokens</th>
                  <th className="text-right p-3 font-medium">Cost</th>
                  <th className="text-right p-3 font-medium">Duration</th>
                  <th className="text-left p-3 font-medium">Date</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((log) => (
                  <tr key={log.id} className="border-b border-white/5 hover:bg-white/[0.02]">
                    <td className="p-3"><input type="checkbox" checked={selected.has(log.id)} onChange={() => toggleSelect(log.id)} /></td>
                    <td className="p-3 font-medium">{log.agent_name}</td>
                    <td className="p-3 text-text-secondary">{log.model}</td>
                    <td className="p-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        log.status === 'success' ? 'bg-success/20 text-success' :
                        log.status === 'error' ? 'bg-error/20 text-error' : 'bg-warning/20 text-warning'
                      }`}>{log.status}</span>
                    </td>
                    <td className="p-3 text-right">{log.messages}</td>
                    <td className="p-3 text-right">{log.tokens.toLocaleString()}</td>
                    <td className="p-3 text-right">${log.cost_usd.toFixed(4)}</td>
                    <td className="p-3 text-right">{(log.duration_ms / 1000).toFixed(1)}s</td>
                    <td className="p-3 text-text-secondary">{new Date(log.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
