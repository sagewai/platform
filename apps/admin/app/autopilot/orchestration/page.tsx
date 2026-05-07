'use client';

import { useCallback, useState } from 'react';
import { GitMerge, RefreshCw } from 'lucide-react';
import type { BlueprintCompositionStep } from '@/utils/types';
import { AutopilotPatternComposition } from '@/components/autopilot-pattern-composition';
import { AutopilotResourceAllocation } from '@/components/autopilot-resource-allocation';
import { AutopilotOperatorDirections } from '@/components/autopilot-operator-directions';

/**
 * Demo composition — lighthouse blueprint shape as publicly documented.
 * All slot values are illustrative placeholders only.
 */
const DEMO_COMPOSITION: BlueprintCompositionStep[] = [
  {
    pattern: 'lighthouse',
    inputs: {
      target_url: 'https://example.com',
      categories: ['performance', 'accessibility', 'seo'],
      output_format: 'json',
    },
  },
  {
    pattern: 'watchdog',
    inputs: {
      schedule: '0 8 * * 1',
      alert_channel: 'slack:#ops',
    },
    wraps: 'lighthouse',
  },
  {
    pattern: 'report-publisher',
    inputs: {
      destination: 's3://reports-bucket/lighthouse/',
      format: 'html',
    },
    wraps: 'watchdog',
  },
];

const DEMO_PAYLOAD = {
  id: 'demo-lighthouse',
  title: 'Lighthouse performance monitor',
  category: 'observability',
  mode: 'scheduled',
  composition: DEMO_COMPOSITION,
};

export default function OrchestrationPage() {
  const [directionKey, setDirectionKey] = useState(0);

  const handleRefresh = useCallback(() => {
    setDirectionKey((k) => k + 1);
  }, []);

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <GitMerge size={18} className="text-primary shrink-0" />
            <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">
              Autopilot orchestration view (preview)
            </h1>
          </div>
          <p className="mt-1 text-sm text-text-secondary">
            Pattern composition → resources → operator directions.
            v1.1 preview; composition retrieval ships in v1.2.
          </p>
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-border bg-transparent cursor-pointer hover:bg-bg-subtle text-text-secondary transition-colors"
        >
          <RefreshCw size={13} />
          Refresh directions
        </button>
      </div>

      {/* Two-column layout: left (composition + resources), right (directions) */}
      <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
        {/* Left column */}
        <div className="space-y-4">
          {/* Pattern composition card */}
          <div className="bg-bg-surface border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border">
              <h2 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
                Pattern composition
              </h2>
              <p className="text-xs text-text-muted m-0 mt-0.5">
                Demo: lighthouse performance monitor
              </p>
            </div>
            <div className="px-5 py-4">
              <AutopilotPatternComposition composition={DEMO_COMPOSITION} />
            </div>
          </div>

          {/* Resource allocation card */}
          <div className="bg-bg-surface border border-border rounded-xl overflow-hidden">
            <div className="px-5 py-4 border-b border-border">
              <h2 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
                Resource allocation
              </h2>
              <p className="text-xs text-text-muted m-0 mt-0.5">
                Workers, sandboxes, and sealed credentials
              </p>
            </div>
            <div className="px-5 py-4">
              <AutopilotResourceAllocation />
            </div>
          </div>
        </div>

        {/* Right column — operator directions */}
        <div className="bg-bg-surface border border-border rounded-xl overflow-hidden flex flex-col">
          <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
                Operator directions
              </h2>
              <p className="text-xs text-text-muted m-0 mt-0.5">
                Generated from{' '}
                <code className="text-[11px] bg-bg-subtle px-1 rounded">
                  /v1/blueprints/explain
                </code>
              </p>
            </div>
          </div>
          <div className="px-5 py-4 flex-1 min-h-0 overflow-auto">
            <AutopilotOperatorDirections
              key={directionKey}
              compositionPayload={DEMO_PAYLOAD}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
