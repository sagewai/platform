// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useEffect, useState } from 'react';
import type { Connection, WebsocketOperation, WebsocketProtocolData } from '@/utils/connection-types';
import { adminApi } from '@/utils/api';

type Props = {
  connection: Connection;
  onRefresh?: () => void | Promise<void>;
};

export function WebsocketPanel({ connection, onRefresh }: Props) {
  const pd = connection.protocol_data as Partial<WebsocketProtocolData>;
  const initialOps = (pd.operations ?? []) as WebsocketOperation[];
  const [ops, setOps] = useState<WebsocketOperation[]>(initialOps);
  const [newName, setNewName] = useState('');
  const [newTemplate, setNewTemplate] = useState('');
  const [newResponseMatch, setNewResponseMatch] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Re-sync local state when the drawer switches to a different connection.
  // useState initializers run only on mount; without this effect, ops would
  // be stale after picking a different WebSocket row in the same drawer mount.
  // Keyed on connection.id only — keying on protocol_data would re-fire on
  // every parent re-render that produces a fresh reference (PR3 lesson).
  useEffect(() => {
    const nextOps = ((connection.protocol_data as Record<string, unknown>).operations as WebsocketOperation[]) ?? [];
    setOps(nextOps);
    setNewName('');
    setNewTemplate('');
    setNewResponseMatch('');
    setErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection.id]);

  async function saveOps(next: WebsocketOperation[]) {
    setSaving(true);
    setErr(null);
    try {
      await adminApi.connections.update(connection.id, {
        protocol_data: { ...(connection.protocol_data ?? {}), operations: next },
      });
      setOps(next);
      if (onRefresh) await onRefresh();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  function addOp() {
    if (!newName || !newTemplate) return;
    if (ops.some(o => o.name === newName)) {
      setErr(`Operation name "${newName}" already exists`);
      return;
    }
    const op: WebsocketOperation = { name: newName, message_template: newTemplate };
    if (newResponseMatch.trim()) {
      op.response_match = newResponseMatch.trim();
    }
    void saveOps([...ops, op]);
    setNewName('');
    setNewTemplate('');
    setNewResponseMatch('');
  }

  function removeOp(name: string) {
    void saveOps(ops.filter(o => o.name !== name));
  }

  const url = pd.url ?? '';
  const authHeaderName = pd.auth_header_name ?? 'Authorization';
  const authHeaderValue = pd.auth_header_value ?? '';
  const defaultTimeout = pd.default_timeout_seconds ?? 30;
  const tierOverride = pd.sandbox_tier_override ?? null;

  return (
    <div className="space-y-6" data-testid="websocket-panel">
      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">Endpoint</h3>
        <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
          <dt className="text-text-tertiary">url</dt>
          <dd className="col-span-2 font-mono">
            {url || <em className="text-text-tertiary">not set</em>}
          </dd>
          <dt className="text-text-tertiary">auth_header_name</dt>
          <dd className="col-span-2 font-mono">{authHeaderName}</dd>
          <dt className="text-text-tertiary">auth_header_value</dt>
          <dd className="col-span-2 font-mono">
            {authHeaderValue === '***'
              ? '*** (encrypted)'
              : <em className="text-text-tertiary">not set</em>}
          </dd>
          <dt className="text-text-tertiary">default timeout</dt>
          <dd className="col-span-2">{defaultTimeout}s</dd>
          <dt className="text-text-tertiary">sandbox tier</dt>
          <dd className="col-span-2">{tierOverride ?? 'SANDBOXED (default)'}</dd>
        </dl>
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">
          Operations (declarative)
        </h3>
        <p className="mt-1 text-xs text-text-tertiary">
          Add send-and-receive operations the agent can invoke by name. Each op declares a{' '}
          <code>message_template</code> (Python <code>{'{key}'}</code> placeholders, filled from
          tool-call kwargs) and an optional <code>response_match</code> (JSONPath like{' '}
          <code>$.price</code>). Without <code>response_match</code> the raw frame is returned.
        </p>

        {err && (
          <p
            className="mt-2 rounded bg-error/10 px-3 py-2 text-xs text-error"
            data-testid="websocket-ops-error"
          >
            {err}
          </p>
        )}

        <table className="mt-3 w-full text-sm" data-testid="websocket-ops-table">
          <thead>
            <tr className="text-left text-text-tertiary">
              <th className="py-1">Name</th>
              <th className="py-1">Message template</th>
              <th className="py-1">Response match</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {ops.length === 0 && (
              <tr>
                <td colSpan={4} className="py-2 text-xs italic text-text-tertiary">
                  No operations declared yet.
                </td>
              </tr>
            )}
            {ops.map(op => (
              <tr key={op.name} className="border-t border-border">
                <td className="font-mono py-1">{op.name}</td>
                <td className="font-mono text-xs py-1">{op.message_template}</td>
                <td className="font-mono text-xs py-1">
                  {op.response_match || <em className="text-text-tertiary">(raw frame)</em>}
                </td>
                <td className="py-1 text-right">
                  <button
                    type="button"
                    onClick={() => removeOp(op.name)}
                    disabled={saving}
                    className="text-xs text-error hover:underline disabled:opacity-50"
                    data-testid={`websocket-remove-op-${op.name}`}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-3 space-y-2">
          <input
            type="text"
            placeholder="op name (e.g., get_quote)"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            data-testid="websocket-new-op-name"
            className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            placeholder='message_template (e.g., {"symbol": "{symbol}"})'
            value={newTemplate}
            onChange={e => setNewTemplate(e.target.value)}
            data-testid="websocket-new-op-template"
            className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            placeholder="response_match (optional JSONPath, e.g., $.price)"
            value={newResponseMatch}
            onChange={e => setNewResponseMatch(e.target.value)}
            data-testid="websocket-new-op-response-match"
            className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
          />
          <button
            type="button"
            onClick={addOp}
            disabled={saving || !newName || !newTemplate}
            className="rounded bg-accent px-3 py-1 text-xs text-text-on-accent disabled:opacity-50"
            data-testid="websocket-add-op-button"
          >
            Add operation
          </button>
        </div>
      </div>
    </div>
  );
}
