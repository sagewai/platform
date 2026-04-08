'use client';

import Link from 'next/link';
import type { AgentSummary } from '@/utils/types';
import { Badge } from '@sagecurator/ui';

interface Props {
  agents: AgentSummary[];
}

export function AgentTable({ agents }: Props) {
  return (
    <div className="bg-bg-surface rounded-lg border border-border overflow-hidden">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide">Name</th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide">Model</th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide">Status</th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide">Capabilities</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr key={agent.name} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
              <td className="py-3 px-4">
                <Link href={`/agents/${encodeURIComponent(agent.name)}`} className="text-primary no-underline font-medium hover:underline">
                  {agent.name}
                </Link>
              </td>
              <td className="py-3 px-4 text-text-muted">{agent.model || '—'}</td>
              <td className="py-3 px-4">
                <Badge variant={agent.status === 'idle' ? 'success' : agent.status === 'running' ? 'info' : agent.status === 'error' ? 'error' : 'default'}>
                  {agent.status}
                </Badge>
              </td>
              <td className="py-3 px-4 text-text-muted">{agent.capabilities.join(', ') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
