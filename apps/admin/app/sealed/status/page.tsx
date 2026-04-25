"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { SealedStatus } from "@/utils/types";

export default function SealedStatusPage() {
  const [status, setStatus] = useState<SealedStatus | null>(null);
  const [showRotate, setShowRotate] = useState(false);

  useEffect(() => {
    adminApi.getSealedStatus().then(setStatus).catch(() => {});
  }, []);

  if (!status) return <div>Loading…</div>;

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-md">Sealed Status</h1>

      <dl className="grid grid-cols-2 gap-2 text-sm border rounded p-md">
        <dt className="text-neutral-500">Master key configured</dt>
        <dd>
          {status.master_key_configured ? (
            <span className="text-green-700">Yes</span>
          ) : (
            <span className="text-rose-600">Not configured</span>
          )}
        </dd>

        <dt className="text-neutral-500">Source</dt>
        <dd className="font-mono">{status.master_key_source}</dd>

        <dt className="text-neutral-500">Last rotated</dt>
        <dd>
          {status.master_key_last_rotated_at
            ? new Date(status.master_key_last_rotated_at).toLocaleString()
            : "—"}
        </dd>

        <dt className="text-neutral-500">Audit retention</dt>
        <dd>{status.audit_retention_days} days</dd>

        <dt className="text-neutral-500">Reveal rate limit</dt>
        <dd>{status.reveal_rate_limit_per_admin_per_hour} per hour</dd>

        <dt className="text-neutral-500">Backends registered</dt>
        <dd className="font-mono">{status.backends_registered.join(", ")}</dd>
      </dl>

      <div className="mt-md">
        <button
          type="button"
          onClick={() => setShowRotate(true)}
          className="rounded border px-3 py-1.5 text-sm cursor-pointer"
        >
          Rotate master key…
        </button>
      </div>

      {showRotate && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center"
          onClick={() => setShowRotate(false)}
        >
          <div
            className="bg-white rounded p-md max-w-lg w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold mb-2">Rotate master key</h2>
            <p className="text-sm mb-2">
              Run this command in your terminal:
            </p>
            <pre className="bg-neutral-100 rounded p-2 text-xs overflow-x-auto">
              sagewai admin sealed rotate-master-key
            </pre>
            <p className="text-xs text-neutral-500 mt-2">
              The CLI prompts for confirmation, generates a new key, re-encrypts all secrets,
              stores the new key in the same place as the old one, and prints a backup phrase.
            </p>
            <button
              type="button"
              onClick={() => setShowRotate(false)}
              className="mt-md rounded bg-blue-600 px-3 py-1.5 text-sm text-white cursor-pointer"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
