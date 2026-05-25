// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

type Props = {
  id: string;
  label: string;
  selected: boolean;
  count?: number;
  onClick: () => void;
};

export function ProtocolChip({ id, label, selected, count, onClick }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`protocol-chip-${id}`}
      data-selected={selected}
      className={`rounded-full border px-3 py-1 text-sm transition-colors ${
        selected
          ? 'border-accent bg-accent text-bg'
          : 'border-border bg-bg text-text-secondary hover:border-text-tertiary'
      }`}
    >
      {label}
      {count !== undefined && <span className="ml-1 opacity-70">({count})</span>}
    </button>
  );
}
