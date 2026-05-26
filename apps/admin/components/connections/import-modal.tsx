// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useState } from 'react';

import { adminApi } from '@/utils/api';
import type { ConflictMode, ImportResult } from '@/utils/connection-types';

interface Props {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}

export function ImportModal({ projectId, open, onClose, onImported }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<ConflictMode>('create-only');
  const [preserveIds, setPreserveIds] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [wasDryRun, setWasDryRun] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!open) return null;

  async function handleFileSelected(f: File | null) {
    if (!f) {
      setFile(null);
      return;
    }
    // Read the first 4KB to look for the top-level secrets_mode declaration.
    // The browser can't supply environment variables, so placeholder-mode
    // imports must go through the CLI (spec: 'Admin UI -> reject placeholder
    // mode at upload step'). Detect via regex — the YAML format guarantees
    // ``secrets_mode:`` appears on its own line at top level.
    try {
      const head = await f.slice(0, 4096).text();
      const match = head.match(/^secrets_mode:\s*(\w+)/m);
      if (match && match[1] === 'placeholder') {
        setErr(
          'Placeholder-mode imports must use the CLI. The browser cannot ' +
            'supply environment variables — run: ' +
            'sagewai connections import < your-file.yaml',
        );
        setFile(null);
        return;
      }
    } catch {
      // Read failed — let the server-side parser surface the error instead.
    }
    setFile(f);
    setErr(null);
  }

  async function submit(dryRun: boolean) {
    if (!file) return;
    setSubmitting(true);
    setErr(null);
    try {
      const res = await adminApi.connections.importYaml(file, {
        project_id: projectId,
        mode,
        dry_run: dryRun,
        preserve_ids: preserveIds,
      });
      setResult(res);
      setWasDryRun(dryRun);
      if (!dryRun && res.errors.length === 0) {
        onImported();
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    setResult(null);
    setWasDryRun(false);
    setErr(null);
  }

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40">
      <div
        className="w-full max-w-lg rounded bg-bg p-6 shadow-xl"
        data-testid="import-modal"
      >
        <h2 className="text-lg font-semibold">Import connections</h2>

        {!result && (
          <div className="mt-4 space-y-3">
            <label className="block">
              <span className="text-sm font-medium">YAML file</span>
              <input
                type="file"
                accept=".yaml,.yml,application/yaml"
                onChange={(e) =>
                  void handleFileSelected(e.target.files?.[0] ?? null)
                }
                data-testid="import-file-input"
                className="mt-1 block text-sm"
              />
            </label>

            <fieldset>
              <legend className="text-xs uppercase text-text-tertiary">Mode</legend>
              {(['create-only', 'upsert', 'skip-existing'] as const).map((m) => (
                <label
                  key={m}
                  className="mt-1 flex items-center gap-2 text-sm"
                >
                  <input
                    type="radio"
                    name="import-mode"
                    checked={mode === m}
                    onChange={() => setMode(m)}
                    data-testid={`import-mode-${m}`}
                  />
                  <span className="font-mono">{m}</span>
                </label>
              ))}
            </fieldset>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={preserveIds}
                onChange={(e) => setPreserveIds(e.target.checked)}
                data-testid="import-preserve-ids"
              />
              <span>Preserve internal IDs</span>
            </label>

            {err && (
              <p
                className="rounded bg-red-50 px-3 py-2 text-xs text-red-900"
                data-testid="import-error"
              >
                {err}
              </p>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded px-3 py-1 text-sm hover:bg-bg-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => submit(true)}
                disabled={!file || submitting}
                className="rounded border border-border px-3 py-1 text-sm hover:bg-bg-secondary disabled:opacity-50"
                data-testid="import-dry-run-button"
              >
                {submitting ? '…' : 'Dry-run'}
              </button>
              <button
                type="button"
                onClick={() => submit(false)}
                disabled={!file || submitting}
                className="rounded bg-accent px-3 py-1 text-sm text-white disabled:opacity-50"
                data-testid="import-submit-button"
              >
                {submitting ? 'Importing…' : 'Import'}
              </button>
            </div>
          </div>
        )}

        {result && (
          <div className="mt-4 space-y-3" data-testid="import-result">
            <h3 className="text-sm font-semibold">
              Import result {wasDryRun ? '(dry run)' : ''}
            </h3>

            {result.created.length > 0 && (
              <div>
                <p className="text-sm font-medium">
                  ✓ {result.created.length} to create:
                </p>
                <ul className="ml-4 list-disc text-xs">
                  {result.created.map((e) => (
                    <li
                      key={`c-${e.protocol}-${e.display_name}`}
                      className="font-mono"
                    >
                      {e.protocol}: {e.display_name}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {result.updated.length > 0 && (
              <div>
                <p className="text-sm font-medium">
                  ↻ {result.updated.length} to update:
                </p>
                <ul className="ml-4 list-disc text-xs">
                  {result.updated.map((e) => (
                    <li
                      key={`u-${e.protocol}-${e.display_name}`}
                      className="font-mono"
                    >
                      {e.protocol}: {e.display_name}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {result.skipped.length > 0 && (
              <div>
                <p className="text-sm font-medium">
                  ⚠ {result.skipped.length} skipped (already exist):
                </p>
                <ul className="ml-4 list-disc text-xs">
                  {result.skipped.map((e) => (
                    <li
                      key={`s-${e.protocol}-${e.display_name}`}
                      className="font-mono"
                    >
                      {e.protocol}: {e.display_name}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {result.errors.length > 0 && (
              <div>
                <p className="text-sm font-medium text-red-700">
                  ✗ {result.errors.length} error
                  {result.errors.length === 1 ? '' : 's'}:
                </p>
                <ul className="ml-4 list-disc text-xs">
                  {result.errors.map((e, i) => (
                    <li key={`e-${i}`} className="font-mono">
                      Row {e.row_index} ({e.protocol}:{e.display_name}):{' '}
                      {e.code}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="mt-4 flex justify-end gap-2">
              {wasDryRun ? (
                <>
                  <button
                    type="button"
                    onClick={reset}
                    className="rounded px-3 py-1 text-sm hover:bg-bg-secondary"
                    data-testid="import-back-button"
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    onClick={() => submit(false)}
                    disabled={submitting || result.errors.length > 0}
                    className="rounded bg-accent px-3 py-1 text-sm text-white disabled:opacity-50"
                    data-testid="import-confirm-button"
                  >
                    {submitting ? 'Importing…' : 'Confirm import'}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded bg-accent px-3 py-1 text-sm text-white"
                  data-testid="import-close-button"
                >
                  Close
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
