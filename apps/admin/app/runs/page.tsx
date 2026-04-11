import { RunsView } from '@/components/runs-view';

export const dynamic = 'force-dynamic';

export default function RunsPage() {
  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Run History</h1>
      <RunsView />
    </div>
  );
}
