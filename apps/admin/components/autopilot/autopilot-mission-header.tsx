'use client';

import type { AutopilotMissionDetail } from '@/utils/types';
import { AutopilotStatusBadge } from '@/components/autopilot-status-badge';

function formatCost(c: AutopilotMissionDetail['estimated_cost']): string | null {
  if (!c) return null;
  // Symbol map for the small set we expect; default to 3-letter code suffix.
  const symbols: Record<string, string> = { USD: '$', EUR: '€', GBP: '£' };
  const sym = symbols[c.currency] ?? '';
  const amt = c.amount.toFixed(2);
  return sym ? `~${sym}${amt}` : `~${amt} ${c.currency}`;
}

export function AutopilotMissionHeader({ mission }: { mission: AutopilotMissionDetail }) {
  const title = mission.goal_text || mission.id;
  const costLabel = formatCost(mission.estimated_cost);

  return (
    <header
      className="flex flex-col gap-3 rounded-lg border border-border bg-bg-surface p-4 lg:flex-row lg:items-center lg:justify-between"
      data-testid="mission-header"
    >
      <div className="flex flex-col gap-1 min-w-0">
        <h1 className="text-lg font-semibold text-text-primary m-0 truncate" title={title}>
          {title}
        </h1>
        <p className="text-xs text-text-muted font-[family-name:var(--font-mono)] m-0">
          mission {mission.id}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <AutopilotStatusBadge status={mission.status} />
        {costLabel ? (
          <span className="text-sm text-text-primary" data-testid="mission-cost">
            {costLabel}
          </span>
        ) : (
          <a
            href="mailto:licensing@sagewai.ai"
            className="text-sm text-primary hover:underline"
            data-testid="mission-licensing-link"
          >
            licensing@sagewai.ai
          </a>
        )}
        <button
          type="button"
          disabled
          title="Implemented in Plan H"
          aria-label="Run mission"
          className="rounded-md bg-primary text-white text-sm px-3 py-1.5 font-medium disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Run mission
        </button>
      </div>
    </header>
  );
}
