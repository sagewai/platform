'use client';

import { EVENT_COLORS } from '@/utils/agui-types';

export function EventBadge({ type }: { type: string }) {
  const color = EVENT_COLORS[type] ?? '#6b7280';
  const label = type.replace(/_/g, ' ');

  return (
    <span
      className="inline-block px-2 py-0.5 rounded text-[11px] font-semibold text-white uppercase tracking-tight whitespace-nowrap"
      style={{ background: color }}
    >
      {label}
    </span>
  );
}
