// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import { AgentGraphSkeleton } from './agent-graph-skeleton';

/** Full-page skeleton matching the mission-detail layout. Real data transitions in
 *  cleanly — parent unmounts this via AnimatePresence so there is no layout jank. */
export function MissionDetailLoadingState() {
  return (
    <div
      data-testid="mission-detail-loading"
      className="p-6 space-y-6"
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2 flex-1">
          <div className="h-7 w-2/5 rounded-lg bg-bg-subtle animate-pulse" data-testid="skeleton-header" />
          <div className="h-4 w-1/3 rounded bg-bg-subtle animate-pulse" />
        </div>
        <div className="h-9 w-24 rounded-lg bg-bg-subtle animate-pulse shrink-0" />
      </div>

      {/* Graph + side panels row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 rounded-lg border border-border bg-bg-surface p-4">
          <AgentGraphSkeleton />
        </div>
        <div className="flex flex-col gap-4">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-40 rounded-lg bg-bg-subtle animate-pulse" data-testid="skeleton-panel" />
          ))}
        </div>
      </div>

      {/* Trace / directions panel */}
      <div className="h-48 w-full rounded-lg bg-bg-subtle animate-pulse" data-testid="skeleton-directions" />
    </div>
  );
}
