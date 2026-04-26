"use client";

import { useState } from "react";
import { adminApi } from "@/utils/api";

interface Props {
  profileId: string;
  secretKey: string;
  onRevoked?: () => void;
}

export function RevokeButton({ profileId, secretKey, onRevoked }: Props) {
  const [showModal, setShowModal] = useState(false);
  const [hard, setHard] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [affectedRuns, setAffectedRuns] = useState<string[]>([]);

  async function loadPreview(useHard: boolean) {
    if (!useHard) {
      setAffectedRuns([]);
      return;
    }
    try {
      const result = await adminApi.previewRevoke(profileId, secretKey);
      setAffectedRuns(result.affected_runs);
    } catch {
      setAffectedRuns([]);
    }
  }

  async function onSubmit() {
    setBusy(true);
    setError(null);
    try {
      await adminApi.revokeSecret({
        profile_id: profileId,
        secret_key: secretKey,
        reason,
        hard,
      });
      setShowModal(false);
      setReason("");
      setHard(false);
      onRevoked?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setShowModal(true)}
        className="text-xs text-rose-600 hover:underline"
      >
        Revoke
      </button>
      {showModal && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={() => !busy && setShowModal(false)}
        >
          <div className="bg-white rounded p-md max-w-lg w-full" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold mb-2">
              Revoke {secretKey} in {profileId}?
            </h2>

            <label className="block mb-3">
              <span className="text-xs">Reason</span>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="block w-full rounded border p-1 text-sm"
                placeholder="e.g. leaked in #engineering-public"
              />
            </label>

            <label className="block mb-3">
              <input
                type="checkbox"
                checked={hard}
                onChange={(e) => {
                  setHard(e.target.checked);
                  loadPreview(e.target.checked);
                }}
              />{" "}
              <span className="text-sm">Hard revoke (also abort in-flight runs)</span>
            </label>

            {!hard && (
              <p className="text-xs text-neutral-500 mb-3">
                Future enqueues + sandbox starts will fail. Runs already in
                flight continue with the value they already injected.
              </p>
            )}

            {hard && (
              <div className="text-xs mb-3">
                {affectedRuns.length === 0 ? (
                  <p className="text-neutral-500">
                    No in-flight runs affected.
                  </p>
                ) : (
                  <>
                    <p className="text-rose-700 mb-1">
                      {affectedRuns.length} in-flight run(s) will be aborted:
                    </p>
                    <ul className="list-disc pl-5 max-h-32 overflow-y-auto">
                      {affectedRuns.slice(0, 10).map((r) => (
                        <li key={r} className="font-mono">{r}</li>
                      ))}
                      {affectedRuns.length > 10 && (
                        <li className="italic">
                          ...and {affectedRuns.length - 10} more
                        </li>
                      )}
                    </ul>
                  </>
                )}
              </div>
            )}

            {error && <div className="text-rose-600 text-sm mb-2">{error}</div>}

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowModal(false)}
                disabled={busy}
                className="rounded border px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={onSubmit}
                disabled={busy || !reason}
                className="rounded bg-rose-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                {busy ? "Revoking\u2026" : hard ? "Hard Revoke" : "Revoke"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
