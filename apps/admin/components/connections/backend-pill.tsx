// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { CredentialsBackendKind } from '@/utils/connection-types';

const BACKEND_STYLES: Record<CredentialsBackendKind, { label: string; cls: string }> = {
  local:   { label: 'local',   cls: 'bg-bg-subtle text-text-secondary border-border' },
  env:     { label: 'env',     cls: 'bg-info/10 text-info border-info/30' },
  sops:    { label: 'sops',    cls: 'bg-accent/10 text-accent border-accent/30' },
  vault:   { label: 'vault',   cls: 'bg-amber-100 text-amber-800 border-amber-300' },
  doppler: { label: 'doppler', cls: 'bg-rose-100 text-rose-800 border-rose-300' },
};

export function BackendPill({ kind }: { kind: CredentialsBackendKind | null }) {
  // null = inherit platform default (which is local for fresh installs)
  const resolved = kind ?? 'local';
  const { label, cls } = BACKEND_STYLES[resolved];
  return (
    <span
      className={`inline-flex rounded border px-2 py-0.5 text-[10px] font-mono ${cls}`}
      data-testid={`backend-pill-${resolved}`}
    >
      {label}
    </span>
  );
}
