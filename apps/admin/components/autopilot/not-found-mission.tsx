// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import Link from 'next/link';
import { SearchX } from 'lucide-react';

export function NotFoundMission({ id }: { id: string }) {
  return (
    <div
      data-testid="not-found-mission"
      className="flex flex-col items-center justify-center py-16 px-6 text-center gap-5"
    >
      <div className="rounded-2xl bg-bg-subtle p-5">
        <SearchX className="size-10 text-text-muted" />
      </div>

      <div className="space-y-1.5 max-w-sm">
        <h1 className="text-xl font-semibold text-text-primary m-0">Mission not found</h1>
        <p className="text-sm text-text-secondary m-0">
          <code className="font-mono text-xs bg-bg-subtle px-1.5 py-0.5 rounded">{id}</code>{' '}
          doesn&apos;t exist or has been deleted.
        </p>
      </div>

      <Link
        href="/autopilot/missions"
        data-testid="back-to-missions-link"
        className="inline-flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-bg-surface text-sm text-text-secondary hover:text-text-primary hover:border-primary transition-colors"
      >
        ← Back to missions
      </Link>
    </div>
  );
}
