'use client';

import { useState } from 'react';
import { adminApi } from '@/utils/api';
import type { ReplayPreview } from '@/utils/types';
import { Button } from '@/components/ui/legacy';

interface Props {
  runId: string;
  fromStep: number;
  stepName: string;
  onReplayed?: (newRunId: string) => void;
}

/**
 * Sealed-iii.C ReplayButton — opens a confirm modal that surfaces preview
 * blockers and warnings, then commits the replay via adminApi.
 */
export function ReplayButton({ runId, fromStep, stepName, onReplayed }: Props) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<ReplayPreview | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function openModal() {
    setErr(null);
    try {
      const p = await adminApi.previewReplay(runId, fromStep);
      setPreview(p);
      setOpen(true);
    } catch (e) {
      setErr(String(e));
    }
  }

  async function commit() {
    setBusy(true);
    setErr(null);
    try {
      const { new_run_id } = await adminApi.commitReplay(
        runId,
        fromStep,
        true,
      );
      onReplayed?.(new_run_id);
      setOpen(false);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const hasBlockers = !!preview && preview.blockers.length > 0;
  const hasWarnings = !!preview && preview.warnings.length > 0;
  const canCommit = !!preview && !hasBlockers && (!hasWarnings || acknowledged);

  return (
    <>
      <Button size="sm" onClick={openModal}>
        Replay from {stepName}
      </Button>
      {err && (
        <span role="alert" className="text-error text-xs ml-sm">
          {err}
        </span>
      )}
      {open && preview && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="replay-title"
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
          onClick={() => setOpen(false)}
        >
          <div
            className="bg-bg-1 p-lg rounded-lg max-w-2xl w-full"
            onClick={e => e.stopPropagation()}
          >
            <h2 id="replay-title" className="text-lg font-bold mb-md">
              Replay run from step <code>{stepName}</code>?
            </h2>

            <p className="text-sm text-text-muted mb-md">
              Mode: <strong>{preview.execution_mode}</strong>
              {preview.security_profile_ref ? (
                <>
                  {' '}
                  · Profile: <code>{preview.security_profile_ref}</code>
                </>
              ) : null}
            </p>

            {hasBlockers && (
              <div className="mb-md">
                <strong className="text-error">Cannot replay — blockers:</strong>
                <ul className="list-disc list-inside text-sm">
                  {preview.blockers.map((b, i) => (
                    <li key={i}>
                      {b.type}
                      {b.step_name ? `: ${b.step_name}` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {hasWarnings && (
              <div className="mb-md">
                <strong>⚠ Warnings:</strong>
                <ul className="list-disc list-inside text-sm">
                  {preview.warnings.map((w, i) => (
                    <li key={i}>
                      {w.type}
                      {w.secret_key ? `: ${w.secret_key}` : ''}
                    </li>
                  ))}
                </ul>
                <label className="block mt-sm text-sm">
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={e => setAcknowledged(e.target.checked)}
                  />{' '}
                  I acknowledge the warnings.
                </label>
              </div>
            )}

            <div className="flex gap-sm justify-end">
              <Button variant="secondary" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={commit}
                disabled={!canCommit || busy}
              >
                {busy ? 'Replaying…' : 'Replay'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
