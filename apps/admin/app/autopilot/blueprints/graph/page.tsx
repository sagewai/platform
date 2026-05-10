import Link from 'next/link';

export default function BlueprintGraphPage() {
  return (
    <div className="space-y-4" data-testid="blueprint-graph-page">
      <div>
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Agent graph
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Visual layout of the agents that will execute your goal.
        </p>
      </div>
      <div className="rounded-lg border border-border bg-bg-surface p-6">
        <p className="text-sm text-text-muted">
          The agent graph is shown once a blueprint is selected. View a{' '}
          <Link href="/autopilot/missions" className="text-primary hover:underline">
            mission in progress
          </Link>{' '}
          to see the live graph.
        </p>
      </div>
    </div>
  );
}
