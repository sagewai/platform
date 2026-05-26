// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useState } from 'react';

import { adminApi } from '@/utils/api';
import type { ProtocolMeta, SecretsMode } from '@/utils/connection-types';

interface Props {
  projectId: string;
  availableProtocols: ProtocolMeta[];
}

export function ExportDropdown({ projectId, availableProtocols }: Props) {
  const [open, setOpen] = useState(false);
  const [secrets, setSecrets] = useState<SecretsMode>('redacted');
  const [selectedProtocols, setSelectedProtocols] = useState<Set<string>>(new Set());
  const [downloading, setDownloading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleDownload() {
    setDownloading(true);
    setErr(null);
    try {
      const blob = await adminApi.connections.exportYaml({
        project_id: projectId,
        secrets,
        protocols: Array.from(selectedProtocols),
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const date = new Date().toISOString().slice(0, 10).replaceAll('-', '');
      a.download = `connections-${projectId || 'default'}-${date}.yaml`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setOpen(false);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  }

  function toggleProtocol(p: string) {
    setSelectedProtocols((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded border border-border px-3 py-1.5 text-sm hover:bg-bg-secondary"
        data-testid="export-yaml-button"
      >
        Export YAML ▾
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-2 w-72 rounded border border-border bg-bg p-4 shadow-lg">
          <h3 className="text-sm font-semibold">Export connections</h3>

          <fieldset className="mt-3">
            <legend className="text-xs uppercase text-text-tertiary">
              Secrets mode
            </legend>
            {(['redacted', 'encrypted', 'placeholder'] as const).map((mode) => (
              <label key={mode} className="mt-1 flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="export-secrets"
                  checked={secrets === mode}
                  onChange={() => setSecrets(mode)}
                  data-testid={`export-secrets-${mode}`}
                />
                <span className="font-mono">{mode}</span>
                {mode === 'redacted' && (
                  <span className="text-xs text-text-tertiary">(safe)</span>
                )}
                {mode === 'encrypted' && (
                  <span className="text-xs text-text-tertiary">(DR only)</span>
                )}
                {mode === 'placeholder' && (
                  <span className="text-xs text-text-tertiary">(cloning)</span>
                )}
              </label>
            ))}
          </fieldset>

          {availableProtocols.length > 0 && (
            <fieldset className="mt-3">
              <legend className="text-xs uppercase text-text-tertiary">
                Filter by protocol (optional)
              </legend>
              <div className="mt-1 grid max-h-32 grid-cols-2 gap-1 overflow-y-auto">
                {availableProtocols.map((p) => (
                  <label key={p.id} className="flex items-center gap-1 text-xs">
                    <input
                      type="checkbox"
                      checked={selectedProtocols.has(p.id)}
                      onChange={() => toggleProtocol(p.id)}
                      data-testid={`export-filter-${p.id}`}
                    />
                    <span className="font-mono">{p.id}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {err && (
            <p
              className="mt-3 rounded bg-red-50 px-3 py-2 text-xs text-red-900"
              data-testid="export-error"
            >
              {err}
            </p>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded px-3 py-1 text-sm hover:bg-bg-secondary"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleDownload}
              disabled={downloading}
              className="rounded bg-accent px-3 py-1 text-sm text-white disabled:opacity-50"
              data-testid="export-download-button"
            >
              {downloading ? 'Downloading…' : 'Download'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
