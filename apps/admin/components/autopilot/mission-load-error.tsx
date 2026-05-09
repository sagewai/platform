// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import Link from 'next/link';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export interface MissionLoadErrorProps {
  id: string;
  message?: string;
  onRetry?: () => void;
}

export function MissionLoadError({ id, message, onRetry }: MissionLoadErrorProps) {
  return (
    <div
      data-testid="mission-load-error"
      className="flex flex-col items-center justify-center py-16 px-6 text-center gap-5"
    >
      <div className="rounded-2xl bg-error/10 p-5">
        <AlertTriangle className="size-10 text-error" />
      </div>

      <div className="space-y-1.5 max-w-sm">
        <h1 className="text-xl font-semibold text-text-primary m-0">Failed to load mission</h1>
        <p className="text-sm text-text-secondary m-0">
          {message ?? `Could not fetch mission ${id}. The server may be unavailable.`}
        </p>
      </div>

      <div className="flex items-center gap-3">
        {onRetry && (
          <button
            type="button"
            data-testid="retry-button"
            onClick={onRetry}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
          >
            <RefreshCw className="size-3.5" aria-hidden />
            Retry
          </button>
        )}
        <Link
          href="/autopilot/missions"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md border border-border bg-bg-surface text-sm text-text-secondary hover:text-text-primary hover:border-primary transition-colors"
        >
          ← Back to missions
        </Link>
      </div>
    </div>
  );
}
