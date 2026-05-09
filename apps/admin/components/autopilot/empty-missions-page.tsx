// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import Link from 'next/link';
import { GoalToBlueprintIllustration } from './illustrations/goal-to-blueprint-illustration';

export function EmptyMissionsPage() {
  return (
    <section
      data-testid="empty-missions-page"
      className="grid md:grid-cols-2 gap-8 py-8 items-center"
    >
      <div className="space-y-4">
        <div>
          <h2 className="text-xl font-semibold text-text-primary m-0 mb-1 font-[family-name:var(--font-heading)]">
            No missions yet.
          </h2>
          <p className="text-sm text-text-secondary m-0">
            Missions appear here once you kick off a goal. Start one from the autopilot home.
          </p>
        </div>
        <Link
          href="/autopilot"
          data-testid="start-goal-link"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          Start with a goal
        </Link>
      </div>

      <GoalToBlueprintIllustration className="w-full max-w-xs justify-self-start md:justify-self-end" />
    </section>
  );
}
