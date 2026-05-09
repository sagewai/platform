// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import type { MissionRunEvent } from '@/utils/types';

export function StepSnapshotPanel({ event }: { event: MissionRunEvent | null }) {
  const [copied, setCopied] = useState(false);

  if (!event) {
    return (
      <div
        data-testid="step-snapshot-empty"
        className="rounded-xl border border-border bg-bg-surface p-4 text-sm text-text-muted"
      >
        Drag the scrubber to inspect a step.
      </div>
    );
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(JSON.stringify(event, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <aside
      data-testid="step-snapshot-panel"
      className="rounded-xl border border-border bg-bg-surface p-4 space-y-3"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-xs px-1.5 py-0.5 rounded bg-bg-subtle text-text-secondary shrink-0">
            {event.kind}
          </span>
          {event.node_id && (
            <span className="text-xs text-text-muted truncate">{event.node_id}</span>
          )}
        </div>
        <button
          type="button"
          onClick={handleCopy}
          aria-label={copied ? 'Copied' : 'Copy JSON'}
          className="shrink-0 flex items-center gap-1 text-xs px-2 py-1 rounded bg-bg-subtle hover:bg-border text-text-secondary transition-colors"
        >
          {copied ? (
            <Check className="size-3 text-success" aria-hidden />
          ) : (
            <Copy className="size-3" aria-hidden />
          )}
          {copied ? 'Copied' : 'Copy JSON'}
        </button>
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        {event.latency_ms != null && (
          <>
            <dt className="text-text-muted">latency</dt>
            <dd className="font-mono text-text-primary">{event.latency_ms} ms</dd>
          </>
        )}
        {event.cost_usd != null && (
          <>
            <dt className="text-text-muted">cost</dt>
            <dd className="font-mono text-text-primary">${event.cost_usd.toFixed(4)}</dd>
          </>
        )}
        {event.model && (
          <>
            <dt className="text-text-muted">model</dt>
            <dd className="font-mono text-text-primary truncate">{event.model}</dd>
          </>
        )}
        {event.input_tokens != null && (
          <>
            <dt className="text-text-muted">tokens in</dt>
            <dd className="font-mono text-text-primary">{event.input_tokens.toLocaleString()}</dd>
          </>
        )}
        {event.output_tokens != null && (
          <>
            <dt className="text-text-muted">tokens out</dt>
            <dd className="font-mono text-text-primary">{event.output_tokens.toLocaleString()}</dd>
          </>
        )}
      </dl>

      {event.output_preview && (
        <div>
          <p className="text-[10px] uppercase tracking-wide text-text-muted mb-1">output preview</p>
          <p className="text-xs text-text-secondary line-clamp-4">{event.output_preview}</p>
        </div>
      )}

      {event.error && (
        <div className="rounded-md bg-error/5 border border-error/20 p-2">
          <p className="text-xs text-error font-mono">{event.error}</p>
        </div>
      )}
    </aside>
  );
}
