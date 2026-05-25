// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { ConnectionStatus } from '@/utils/connection-types';

const STATUS_STYLES: Record<ConnectionStatus, { label: string; cls: string; icon: string }> = {
  ready:   { label: 'Ready',   cls: 'bg-success/10 text-success border-success/30',           icon: '●' },
  pending: { label: 'Pending', cls: 'bg-bg-subtle text-text-secondary border-border',         icon: '○' },
  expired: { label: 'Expired', cls: 'bg-warning/10 text-warning border-warning/30',           icon: '⚠' },
  revoked: { label: 'Revoked', cls: 'bg-bg-subtle text-text-secondary border-border line-through', icon: '⊘' },
  error:   { label: 'Error',   cls: 'bg-error/10 text-error border-error/30',                 icon: '✕' },
};

export function StatusPill({ status }: { status: ConnectionStatus }) {
  const { label, cls, icon } = STATUS_STYLES[status];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${cls}`}
      data-testid={`status-pill-${status}`}
    >
      <span aria-hidden>{icon}</span>
      <span>{label}</span>
    </span>
  );
}
