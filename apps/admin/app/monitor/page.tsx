import { MonitorView } from '@/components/monitor-view';

export const dynamic = 'force-dynamic';

export default function MonitorPage() {
  return (
    <div>
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Execution Monitor</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Real-time agent execution viewer — subscribe to AG-UI events and watch agents think.
      </p>
      <MonitorView />
    </div>
  );
}
