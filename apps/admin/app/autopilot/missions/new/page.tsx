import Link from 'next/link';
import { AutopilotGoalInput } from '@/components/autopilot-goal-input';

export default function NewMissionPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          New goal
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Describe a goal in plain English. Autopilot routes it to the right blueprint.
        </p>
      </div>
      <div className="bg-bg-surface border border-border rounded-xl px-5 py-4">
        <AutopilotGoalInput />
      </div>
      <p className="text-xs text-text-muted">
        <Link href="/autopilot/missions" className="text-primary hover:underline">
          ← Back to missions
        </Link>
      </p>
    </div>
  );
}
