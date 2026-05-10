import Link from 'next/link';

export default function BlueprintPickerPage() {
  return (
    <div className="space-y-4" data-testid="blueprint-picker-page">
      <div>
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Blueprint picker
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Choose from candidate blueprints matched to your goal.
        </p>
      </div>
      <div className="rounded-lg border border-border bg-bg-surface p-6 space-y-3">
        <p className="text-sm text-text-muted">
          Blueprint candidates are surfaced after routing. Submit a goal via the{' '}
          <Link href="/autopilot" className="text-primary hover:underline">
            Goals page
          </Link>{' '}
          to see candidates.
        </p>
      </div>
    </div>
  );
}
