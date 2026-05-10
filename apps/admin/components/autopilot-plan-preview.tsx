'use client';

import { useState } from 'react';
import { CheckCircle, X } from 'lucide-react';
import { Spinner } from '@/components/ui/spinner';
import { adminApi } from '@/utils/api';
import type { AutopilotBlueprint } from '@/utils/types';

const MODE_LABELS: Record<string, string> = {
  scheduled: 'Scheduled',
  event_driven: 'Event-driven',
  batch: 'Batch',
};

interface AutopilotPlanPreviewProps {
  blueprint: AutopilotBlueprint;
  missionId: string;
  onApproved: () => void;
  onCancel: () => void;
}

export function AutopilotPlanPreview({
  blueprint,
  missionId,
  onApproved,
  onCancel,
}: AutopilotPlanPreviewProps) {
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleApprove() {
    setApproving(true);
    setError(null);
    try {
      await adminApi.approveAutopilotMission(missionId, blueprint.id);
      onApproved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approval failed.');
      setApproving(false);
    }
  }

  return (
    <div className="bg-bg-surface border border-border rounded-xl overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-text-muted m-0 mb-1">
            Blueprint matched
          </p>
          <h3 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
            {blueprint.title}
          </h3>
          <p className="text-sm text-text-secondary m-0 mt-0.5">{blueprint.category}</p>
        </div>
        <span className="shrink-0 inline-block text-[11px] font-semibold uppercase tracking-wide bg-primary/10 text-primary px-2.5 py-1 rounded-full">
          {MODE_LABELS[blueprint.mode] ?? blueprint.mode}
        </span>
      </div>

      {/* Slots */}
      {blueprint.slots.length > 0 && (
        <div className="px-5 py-4 border-b border-border">
          <p className="text-xs font-semibold uppercase tracking-widest text-text-muted m-0 mb-3">
            Extracted parameters
          </p>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2">
            {blueprint.slots.map((slot) => (
              <div key={slot.key}>
                <dt className="text-[11px] font-semibold text-text-muted uppercase tracking-wide">
                  {slot.key}
                </dt>
                <dd className="text-sm text-text-primary font-[family-name:var(--font-mono)] m-0 mt-0.5 break-all">
                  {slot.value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {/* Estimated cost */}
      <div className="px-5 py-3 border-b border-border flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-widest text-text-muted">
          Estimated cost
        </span>
        <span className="text-sm text-text-primary">
          {blueprint.estimated_cost ?? (
            <a
              href="mailto:licensing@sagewai.ai"
              className="text-primary underline underline-offset-2 hover:no-underline"
            >
              Contact licensing@sagewai.ai for custom rates
            </a>
          )}
        </span>
      </div>

      {/* Error */}
      {error && (
        <div className="px-5 py-2 bg-error/5">
          <p className="text-sm text-error m-0">{error}</p>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 px-5 py-3">
        <button
          type="button"
          onClick={onCancel}
          disabled={approving}
          className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-border bg-transparent cursor-pointer hover:bg-bg-subtle text-text-secondary transition-colors disabled:opacity-40"
        >
          <X size={14} />
          Cancel
        </button>
        <button
          type="button"
          onClick={handleApprove}
          disabled={approving}
          className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold rounded-lg bg-primary text-white border-none cursor-pointer hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {approving ? (
            <Spinner size={14} />
          ) : (
            <CheckCircle size={14} />
          )}
          {approving ? 'Scheduling…' : 'Approve & Schedule'}
        </button>
      </div>
    </div>
  );
}
