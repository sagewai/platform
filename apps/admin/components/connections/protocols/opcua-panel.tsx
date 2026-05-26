// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useEffect, useState } from 'react';
import type { Connection, OpcuaOperation, OpcuaProtocolData } from '@/utils/connection-types';
import { adminApi } from '@/utils/api';

type Props = {
  connection: Connection;
  onRefresh?: () => void | Promise<void>;
};

export function OpcuaPanel({ connection, onRefresh }: Props) {
  const pd = connection.protocol_data as Partial<OpcuaProtocolData>;
  const initialOps = (pd.operations ?? []) as OpcuaOperation[];
  const [ops, setOps] = useState<OpcuaOperation[]>(initialOps);
  const [newName, setNewName] = useState('');
  const [newNodeId, setNewNodeId] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Re-sync local state when the drawer switches to a different connection.
  // useState initializers run only on mount; without this effect, ops would
  // be stale after picking a different OPC UA row in the same drawer mount.
  // Keyed on connection.id only — keying on protocol_data would re-fire on
  // every parent re-render that produces a fresh reference. Changes to
  // protocol_data WITHIN the same connection are driven by saveOps below
  // (which sets ops directly) and by onRefresh callbacks from the parent.
  useEffect(() => {
    const nextOps = ((connection.protocol_data as Record<string, unknown>).operations as OpcuaOperation[]) ?? [];
    setOps(nextOps);
    setNewName('');
    setNewNodeId('');
    setErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection.id]);

  async function saveOps(next: OpcuaOperation[]) {
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
    if (!newName || !newNodeId) return;
    if (ops.some(o => o.name === newName)) {
      setErr(`Operation name "${newName}" already exists`);
      return;
    }
    void saveOps([...ops, { name: newName, kind: 'read', node_id: newNodeId }]);
    setNewName('');
    setNewNodeId('');
  }

  function removeOp(name: string) {
    void saveOps(ops.filter(o => o.name !== name));
  }

  const endpointUrl = pd.endpoint_url ?? '';
  const securityMode = pd.security_mode ?? 'None';
  const securityPolicy = pd.security_policy ?? 'None';
  const authMode = pd.auth_mode ?? 'anonymous';
  const username = pd.username ?? '';
  const password = pd.password ?? '';
  const tierOverride = pd.sandbox_tier_override ?? null;

  return (
    <div className="space-y-6" data-testid="opcua-panel">
      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">Endpoint</h3>
        <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
          <dt className="text-text-tertiary">endpoint_url</dt>
          <dd className="col-span-2 font-mono">
            {endpointUrl || <em className="text-text-tertiary">not set</em>}
          </dd>
          <dt className="text-text-tertiary">security_mode</dt>
          <dd className="col-span-2 font-mono">
            {securityMode}
            {securityMode === 'None' && (
              <span className="ml-2 text-xs text-text-tertiary">(Phase A — Phase B unlocks Sign / SignAndEncrypt)</span>
            )}
          </dd>
          <dt className="text-text-tertiary">security_policy</dt>
          <dd className="col-span-2 font-mono">
            {securityPolicy}
            {securityPolicy === 'None' && (
              <span className="ml-2 text-xs text-text-tertiary">(Phase A — Phase B unlocks Basic256Sha256 et al.)</span>
            )}
          </dd>
          <dt className="text-text-tertiary">auth_mode</dt>
          <dd className="col-span-2">{authMode}</dd>
          {authMode === 'username' && (
            <>
              <dt className="text-text-tertiary">username</dt>
              <dd className="col-span-2 font-mono">{username}</dd>
              <dt className="text-text-tertiary">password</dt>
              <dd className="col-span-2 font-mono">
                {password === '***' ? '*** (encrypted)' : <em className="text-text-tertiary">not set</em>}
              </dd>
            </>
          )}
          <dt className="text-text-tertiary">sandbox tier</dt>
          <dd className="col-span-2">{tierOverride ?? 'SANDBOXED (default)'}</dd>
        </dl>
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">
          Operations (declarative)
        </h3>
        <p className="mt-1 text-xs text-text-tertiary">
          Add read operations the agent can invoke by name. Phase A is read-only;
          method calls and subscriptions are deferred.
        </p>

        {err && (
          <p
            className="mt-2 rounded bg-error/10 px-3 py-2 text-xs text-error"
            data-testid="opcua-ops-error"
          >
            {err}
          </p>
        )}

        <table className="mt-3 w-full text-sm" data-testid="opcua-ops-table">
          <thead>
            <tr className="text-left text-text-tertiary">
              <th className="py-1">Name</th>
              <th className="py-1">Node ID</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {ops.length === 0 && (
              <tr>
                <td colSpan={3} className="py-2 text-xs italic text-text-tertiary">
                  No operations declared yet.
                </td>
              </tr>
            )}
            {ops.map(op => (
              <tr key={op.name} className="border-t border-border">
                <td className="font-mono py-1">{op.name}</td>
                <td className="font-mono text-xs py-1">{op.node_id}</td>
                <td className="py-1 text-right">
                  <button
                    type="button"
                    onClick={() => removeOp(op.name)}
                    disabled={saving}
                    className="text-xs text-error hover:underline disabled:opacity-50"
                    data-testid={`opcua-remove-op-${op.name}`}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-3 flex gap-2">
          <input
            type="text"
            placeholder="op name (e.g., read_temperature)"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            data-testid="opcua-new-op-name"
            className="flex-1 rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            placeholder="node_id (e.g., ns=2;s=Temp)"
            value={newNodeId}
            onChange={e => setNewNodeId(e.target.value)}
            data-testid="opcua-new-op-node-id"
            className="flex-1 rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
          />
          <button
            type="button"
            onClick={addOp}
            disabled={saving || !newName || !newNodeId}
            className="rounded bg-accent px-3 py-1 text-xs text-text-on-accent disabled:opacity-50"
            data-testid="opcua-add-op-button"
          >
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
