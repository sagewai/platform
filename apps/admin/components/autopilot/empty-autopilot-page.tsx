// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { useEffect, useState } from 'react';

import { adminApi } from '@/utils/api';
import { GoalToBlueprintIllustration } from './illustrations/goal-to-blueprint-illustration';

// Fallback shown if the blueprint service can't supply real example goals
// (offline, or an older sagewai-llm without /v1/examples). On success these are
// replaced by a random sample of real goals from the ~200 golden blueprints, so
// clicking one retrieves that blueprint directly instead of synthesizing.
const FALLBACK_GOALS = [
  'Summarize my unread inbox into 5 bullets',
  'Find a flight from Berlin to Lisbon under €120',
  'Generate a weekly status report from my calendar',
];

export interface EmptyAutopilotPageProps {
  onPickGoal: (goal: string) => void;
}

export function EmptyAutopilotPage({ onPickGoal }: EmptyAutopilotPageProps) {
  const [sampleGoals, setSampleGoals] = useState<string[]>(FALLBACK_GOALS);

  useEffect(() => {
    let cancelled = false;
    adminApi
      .getAutopilotExamples()
      .then((res) => {
        if (!cancelled && res?.examples?.length) setSampleGoals(res.examples);
      })
      .catch(() => {
        /* keep the fallback goals */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section
      data-testid="empty-autopilot-page"
      className="grid md:grid-cols-2 gap-8 py-8"
    >
      <div className="space-y-5">
        <div>
          <h2 className="text-xl font-semibold text-text-primary m-0 mb-1 font-[family-name:var(--font-heading)]">
            Start with a goal.
          </h2>
          <p className="text-sm text-text-secondary m-0">
            Tell Sagewai what you want done — we&apos;ll find a blueprint and run it for you.
          </p>
        </div>

        <div>
          <p className="text-xs font-medium text-text-muted uppercase tracking-wide mb-2">
            Try these →
          </p>
          <div className="flex flex-wrap gap-2">
            {sampleGoals.map((g) => (
              <button
                key={g}
                type="button"
                data-testid="sample-goal-pill"
                onClick={() => onPickGoal(g)}
                className="px-3 py-1.5 rounded-full border border-border bg-bg-surface text-sm text-text-secondary hover:border-primary hover:text-text-primary transition-colors cursor-pointer"
              >
                {g}
              </button>
            ))}
          </div>
        </div>
      </div>

      <GoalToBlueprintIllustration className="w-full max-w-xs justify-self-start md:justify-self-end self-center" />
    </section>
  );
}
