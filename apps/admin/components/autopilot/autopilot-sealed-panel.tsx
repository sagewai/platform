'use client';

import { useCallback, useEffect, useState } from 'react';

// ── types ─────────────────────────────────────────────────────────────────

interface StepAllocation {
  step_id: string;
  role: string | null;
  tools: string[];
  required_scopes: string[];
  matched_profile_id: string | null;
  overridden: boolean;
  jit_hitl: boolean;
}

// ── JIT-HITL pending pill ─────────────────────────────────────────────────

function JitHitlPill({ stepId }: { stepId: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-[10px] font-semibold text-warning"
      data-testid="jit-hitl-pill"
      title={`Step ${stepId} has no matching Sealed profile — awaiting operator approval`}
    >
      JIT-HITL pending
    </span>
  );
}

// ── Override modal ────────────────────────────────────────────────────────

function OverrideModal({
  step,
  missionId,
  onSuccess,
  onCancel,
}: {
  step: StepAllocation;
  missionId: string;
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [profileId, setProfileId] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const apply = useCallback(async () => {
    if (!profileId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(
        `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/sealed-override`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ step_id: step.step_id, profile_id: profileId.trim() }),
        },
      );
      if (resp.ok) {
        onSuccess();
      } else {
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        setError(body.detail ?? 'Override failed');
      }
    } catch {
      setError('Network error');
    } finally {
      setLoading(false);
    }
  }, [profileId, missionId, step.step_id, onSuccess]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-bg-surface rounded-lg border border-border p-6 w-80 shadow-lg">
        <h4 className="text-sm font-semibold text-text-primary mb-1">
          Assign Sealed profile — {step.step_id}
        </h4>
        <p className="text-xs text-text-secondary mb-3">
          Required scopes:{' '}
          <span className="font-mono">{step.required_scopes.join(', ') || 'none'}</span>
        </p>
        <input
          type="text"
          placeholder="Profile ID"
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          className="w-full rounded border border-border bg-bg-input px-2 py-1.5 text-sm text-text-primary mb-3 focus:outline-none focus:ring-1 focus:ring-primary"
        />
        {error && <p className="text-xs text-error mb-2">{error}</p>}
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs rounded border border-border text-text-secondary hover:bg-bg-subtle"
          >
            Cancel
          </button>
          <button
            onClick={apply}
            disabled={loading || !profileId.trim()}
            className="px-3 py-1.5 text-xs rounded bg-primary text-white hover:bg-primary/90 disabled:opacity-50"
          >
            {loading ? 'Applying…' : 'Assign profile'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Step row ──────────────────────────────────────────────────────────────

function SealedStepRow({
  step,
  missionId,
  onOverrideApplied,
}: {
  step: StepAllocation;
  missionId: string;
  onOverrideApplied: () => void;
}) {
  const [showModal, setShowModal] = useState(false);

  return (
    <>
      <li className="flex items-start gap-3 py-2 border-t border-border first:border-t-0">
        <div className="flex flex-col gap-1 min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-[family-name:var(--font-mono)] text-text-secondary text-xs w-28 truncate shrink-0">
              {step.step_id}
            </span>
            {step.role && (
              <span className="text-text-secondary text-xs shrink-0">{step.role}</span>
            )}
          </div>
          {step.required_scopes.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {step.required_scopes.map((s) => (
                <span
                  key={s}
                  className="rounded-full bg-bg-subtle border border-border text-text-secondary text-[10px] px-1.5 py-0.5 font-[family-name:var(--font-mono)]"
                >
                  {s}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {step.jit_hitl ? (
            <JitHitlPill stepId={step.step_id} />
          ) : step.matched_profile_id ? (
            <span className="text-xs text-success font-[family-name:var(--font-mono)]">
              {step.matched_profile_id}
              {step.overridden && (
                <span className="ml-1 text-[8px] opacity-70" title="Manually overridden">
                  ✎
                </span>
              )}
            </span>
          ) : (
            <span className="text-xs text-text-secondary">No scopes required</span>
          )}
          <button
            onClick={() => setShowModal(true)}
            className="text-[10px] text-primary hover:underline"
            title="Assign Sealed profile"
          >
            assign
          </button>
        </div>
      </li>
      {showModal && (
        <OverrideModal
          step={step}
          missionId={missionId}
          onSuccess={() => {
            setShowModal(false);
            onOverrideApplied();
          }}
          onCancel={() => setShowModal(false)}
        />
      )}
    </>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────

export function AutopilotSealedPanel({ missionId }: { missionId: string }) {
  const [allocation, setAllocation] = useState<StepAllocation[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAllocation = useCallback(async () => {
    try {
      const resp = await fetch(
        `/api/v1/autopilot/missions/${encodeURIComponent(missionId)}/sealed-allocation`,
        { credentials: 'include' },
      );
      if (resp.ok) {
        const data = (await resp.json()) as StepAllocation[];
        setAllocation(data);
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

  const pendingCount = allocation.filter((s) => s.jit_hitl).length;

  return (
    <section
      className="rounded-lg border border-border bg-bg-surface p-4"
      data-testid="sealed-panel"
    >
      <div className="flex items-center gap-2 mb-3">
        <h3 className="text-sm font-semibold text-text-primary">Sealed profiles</h3>
        {pendingCount > 0 && (
          <span
            className="rounded-full bg-warning/10 border border-warning/30 text-warning text-[10px] font-semibold px-2 py-0.5"
            data-testid="jit-hitl-count"
          >
            {pendingCount} JIT-HITL
          </span>
        )}
      </div>
      {loading ? (
        <p className="text-sm text-text-secondary">Loading…</p>
      ) : allocation.length === 0 ? (
        <p className="text-sm text-text-secondary">No agent steps to display.</p>
      ) : (
        <ol className="list-none p-0 m-0">
          {allocation.map((step) => (
            <SealedStepRow
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
