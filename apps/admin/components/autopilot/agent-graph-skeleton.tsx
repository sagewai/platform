// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri

/** Graph-shaped skeleton shown while the mission detail loads — more semantically
 *  informative than generic shimmer rectangles. */
export function AgentGraphSkeleton() {
  return (
    <div
      role="status"
      aria-label="Loading mission graph"
      data-testid="agent-graph-skeleton"
      className="relative w-full h-60"
    >
      <svg
        viewBox="0 0 400 240"
        className="w-full h-full animate-pulse"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id="agSkelGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--color-bg-subtle)" />
            <stop offset="100%" stopColor="var(--color-border)" />
          </linearGradient>
        </defs>

        {/* Edges — drawn before circles so circles render on top */}
        <line x1="72" y1="120" x2="168" y2="68" stroke="url(#agSkelGrad)" strokeWidth="2" data-testid="skeleton-edge" />
        <line x1="72" y1="120" x2="168" y2="172" stroke="url(#agSkelGrad)" strokeWidth="2" data-testid="skeleton-edge" />
        <line x1="232" y1="68" x2="328" y2="120" stroke="url(#agSkelGrad)" strokeWidth="2" data-testid="skeleton-edge" />
        <line x1="232" y1="172" x2="328" y2="120" stroke="url(#agSkelGrad)" strokeWidth="2" data-testid="skeleton-edge" />

        {/* Nodes */}
        <circle cx="72" cy="120" r="24" fill="url(#agSkelGrad)" data-testid="skeleton-node" />
        <circle cx="200" cy="68" r="24" fill="url(#agSkelGrad)" data-testid="skeleton-node" />
        <circle cx="200" cy="172" r="24" fill="url(#agSkelGrad)" data-testid="skeleton-node" />
        <circle cx="328" cy="120" r="24" fill="url(#agSkelGrad)" data-testid="skeleton-node" />
      </svg>
    </div>
  );
}
