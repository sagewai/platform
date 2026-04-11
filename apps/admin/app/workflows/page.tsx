import { WorkflowEditor } from '@/components/workflow-editor';
import { WorkflowStatsBar } from '@/components/workflow-stats-bar';
import { TourTrigger } from '@/components/tour-trigger';

export const dynamic = 'force-dynamic';

export default function WorkflowsPage() {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">Workflow Builder</h1>
        <TourTrigger tourId="workflow-builder" />
      </div>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Design agent pipelines with YAML, preview the execution graph, and test with live input.
      </p>
      <WorkflowStatsBar />
      <div data-tour="workflow-editor">
        <WorkflowEditor />
      </div>
    </div>
  );
}
