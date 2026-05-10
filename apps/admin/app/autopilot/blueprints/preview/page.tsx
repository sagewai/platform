import Link from 'next/link';

export default function BlueprintPreviewPage() {
  return (
    <div className="space-y-4" data-testid="blueprint-preview-page">
      <div>
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Blueprint preview
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Review the routing decision before running your goal.
        </p>
      </div>
      <div className="rounded-lg border border-border bg-bg-surface p-6 space-y-3">
        <p className="text-sm text-text-muted">
          Blueprint preview is generated after submitting a goal. Navigate via the{' '}
          <Link href="/autopilot" className="text-primary hover:underline">
            Goals page
          </Link>{' '}
          to submit a new goal.
        </p>
      </div>
    </div>
  );
}
