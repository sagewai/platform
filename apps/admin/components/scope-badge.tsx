'use client';

import { Badge } from '@/components/ui/legacy';

const SCOPE_COLORS: Record<string, string> = {
  org: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  project: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
};

const SCOPE_LABELS: Record<string, string> = {
  org: 'Organization',
  project: 'Project',
};

export function ScopeBadge({ scope }: { scope: string }) {
  const colorClass = SCOPE_COLORS[scope] ?? 'bg-gray-500/15 text-gray-400 border-gray-500/30';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium border ${colorClass}`}
    >
      {SCOPE_LABELS[scope] ?? scope}
    </span>
  );
}
