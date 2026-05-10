'use client';

import { useCallback, useEffect, useState } from 'react';
import { TierBadge } from './tier-badge';

// ── types ─────────────────────────────────────────────────────────────────

interface StepAllocation {
  step_id: string;
  role: string | null;
  tools: string[];
  tier: string;
  base_tier: string;
  overridden: boolean;
}

// ── Override confirmation modal ───────────────────────────────────────────

function OverrideModal({
  step,
  onConfirm,
  onCancel,
}: {
  step: StepAllocation;
  onConfirm: (tier: string) => void;
  onCancel: () => void;
}) {
  const tiers = ['TRUSTED', 'SANDBOXED', 'UNTRUSTED'];
  const [selected, setSelected] = useState(step.tier);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-bg-surface rounded-lg border border-border p-6 w-80 shadow-lg">
        <h4 className="text-sm font-semibold text-text-primary mb-3">
          Override sandbox tier — {step.step_id}
        </h4>
        <p className="text-xs text-text-secondary mb-4">
          Only downgrades (to a less trusted tier) are accepted. Current:{' '}
          <span className="font-medium">{step.tier}</span>
        </p>
        <div className="flex flex-col gap-2 mb-5">
          {tiers.map((t) => (
            <label key={t} className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="radio"
                name="tier"
                value={t}
                checked={selected === t}
                onChange={() => setSelected(t)}
                className="accent-primary"
              />
              <TierBadge tier={t} />
            </label>
          ))}
        </div>
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs rounded border border-border text-text-secondary hover:bg-bg-subtle"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(selected)}
            className="px-3 py-1.5 text-xs rounded bg-primary text-text-on-dark hover:bg-primary/90 motion-safe:active:scale-[0.98] transition-transform duration-75"
          >
            Apply override
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Step row ──────────────────────────────────────────────────────────────

function SandboxStepRow({
  step,
  missionId,
  onOverrideApplied,
}: {
  step: StepAllocation;
  missionId: string;
  onOverrideApplied: () => void;
}) {
  const [showModal, setShowModal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyOverride = useCallback(
    async (tier: string) => {
      setError(null);
      try {
        const resp = await fetch(
          `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/sandbox-override`,
          {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ step_id: step.step_id, tier }),
          },
        );
        if (resp.ok) {
          setShowModal(false);
          onOverrideApplied();
        } else {
          const body = (await resp.json().catch(() => ({}))) as { detail?: string };
          setError(body.detail ?? 'Override rejected');
        }
      } catch {
        setError('Network error');
      }
    },
    [missionId, step.step_id, onOverrideApplied],
  );

  return (
    <>
      <li className="flex items-center gap-3 py-2 border-t border-border first:border-t-0">
        <span className="font-[family-name:var(--font-mono)] text-text-secondary text-xs w-28 truncate shrink-0">
          {step.step_id}
        </span>
        {step.role && (
          <span className="text-text-secondary text-xs shrink-0">{step.role}</span>
        )}
        <div className="flex gap-1 flex-wrap">
          {step.tools.slice(0, 3).map((t) => (
            <span
              key={t}
              className="rounded-full bg-bg-subtle border border-border text-text-secondary text-[10px] px-1.5 py-0.5 font-[family-name:var(--font-mono)]"
            >
              {t}
            </span>
          ))}
          {step.tools.length > 3 && (
            <span className="text-text-secondary text-[10px]">+{step.tools.length - 3}</span>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <TierBadge tier={step.tier} overridden={step.overridden} />
          <button
            onClick={() => setShowModal(true)}
            className="text-[10px] text-primary hover:underline"
            title="Override sandbox tier"
          >
            override
          </button>
        </div>
      </li>
      {error && (
        <li className="py-1 px-2 text-xs text-error">{error}</li>
      )}
      {showModal && (
        <OverrideModal
          step={step}
          onConfirm={applyOverride}
          onCancel={() => setShowModal(false)}
        />
      )}
    </>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────

export function AutopilotSandboxPanel({ missionId }: { missionId: string }) {
  const [allocation, setAllocation] = useState<StepAllocation[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAllocation = useCallback(async () => {
    try {
      const resp = await fetch(
        `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/sandbox-allocation`,
        { credentials: 'include' },
      );
      if (resp.ok) {
        const data = (await resp.json()) as StepAllocation[];
        if (Array.isArray(data)) setAllocation(data);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [missionId]);

  useEffect(() => {
    void fetchAllocation();
  }, [fetchAllocation]);

  return (
    <section
      className="rounded-lg border border-border bg-bg-surface p-4"
      data-testid="sandbox-panel"
    >
      <h3 className="text-sm font-semibold text-text-primary mb-3">Sandbox tiers</h3>
      {loading ? (
        <p className="text-sm text-text-secondary">Loading…</p>
      ) : allocation.length === 0 ? (
        <p className="text-sm text-text-secondary">No agent steps to display.</p>
      ) : (
        <ol className="list-none p-0 m-0">
          {allocation.map((step) => (
            <SandboxStepRow
              key={step.step_id}
              step={step}
              missionId={missionId}
              onOverrideApplied={fetchAllocation}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
