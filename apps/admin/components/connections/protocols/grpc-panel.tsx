// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useCallback, useEffect, useState } from 'react';
import type { Connection, GrpcProtocolData, GrpcStream } from '@/utils/connection-types';
import { adminApi } from '@/utils/api';

type Props = {
  connection: Connection;
  onRefresh?: () => void | Promise<void>;
};

export function GrpcPanel({ connection }: Props) {
  const pd = connection.protocol_data as Partial<GrpcProtocolData>;
  const target = pd.target ?? '';
  const tls = pd.tls ?? 'tls';
  const authMode = pd.auth_mode ?? 'none';
  const authKey = pd.auth_metadata_key ?? 'authorization';
  const authToken = pd.auth_token ?? '';
  const timeout = pd.default_timeout_seconds ?? 30;

  const tlsLabel =
    tls === 'insecure'
      ? 'insecure (plaintext)'
      : tls === 'tls_ca'
        ? 'TLS (custom CA)'
        : 'TLS (system trust)';

  // ── Streams (server-streaming subscriptions) ──────────────────────
  const [streams, setStreams] = useState<GrpcStream[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [method, setMethod] = useState('');
  const [requestJson, setRequestJson] = useState('{}');
  const [drainOutput, setDrainOutput] = useState<string | null>(null);

  const loadStreams = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await adminApi.connections.grpc.listSubscriptions();
      // Only show streams bound to THIS connection.
      setStreams(rows.filter(s => s.connection_id === connection.id));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [connection.id]);

  // Re-sync when the drawer switches to a different connection. Keyed on
  // connection.id only (PR3 lesson: keying on protocol_data re-fires on
  // every parent re-render that produces a fresh reference).
  useEffect(() => {
    setMethod('');
    setRequestJson('{}');
    setDrainOutput(null);
    setErr(null);
    void loadStreams();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection.id]);

  async function addStream() {
    if (!method.trim()) return;
    setLoading(true);
    setErr(null);
    let request: Record<string, unknown> = {};
    try {
      const parsed = JSON.parse(requestJson || '{}');
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        request = parsed as Record<string, unknown>;
      } else {
        throw new Error('request must be a JSON object');
      }
    } catch (e: unknown) {
      setErr(`Invalid request JSON: ${e instanceof Error ? e.message : String(e)}`);
      setLoading(false);
      return;
    }
    try {
      await adminApi.connections.grpc.subscribe(connection.id, {
        method: method.trim(),
        request,
      });
      setMethod('');
      setRequestJson('{}');
      await loadStreams();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function unsubscribe(subId: string) {
    setLoading(true);
    setErr(null);
    try {
      await adminApi.connections.grpc.unsubscribe(subId);
      await loadStreams();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function drainDebug(subId: string) {
    setLoading(true);
    setErr(null);
    try {
      const dr = await adminApi.connections.grpc.drain(subId, 50);
      setDrainOutput(JSON.stringify(dr, null, 2));
      await loadStreams();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6" data-testid="grpc-panel">
      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">Target</h3>
        <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
          <dt className="text-text-tertiary">target</dt>
          <dd className="col-span-2 font-mono">
            {target || <em className="text-text-tertiary">not set</em>}
          </dd>
          <dt className="text-text-tertiary">TLS</dt>
          <dd className="col-span-2">{tlsLabel}</dd>
          <dt className="text-text-tertiary">auth mode</dt>
          <dd className="col-span-2">{authMode}</dd>
          {authMode === 'metadata_token' && (
            <>
              <dt className="text-text-tertiary">metadata key</dt>
              <dd className="col-span-2 font-mono">{authKey}</dd>
              <dt className="text-text-tertiary">token</dt>
              <dd className="col-span-2 font-mono">
                {authToken === '***' ? (
                  '*** (encrypted)'
                ) : (
                  <em className="text-text-tertiary">not set</em>
                )}
              </dd>
            </>
          )}
          <dt className="text-text-tertiary">timeout</dt>
          <dd className="col-span-2">{timeout}s</dd>
        </dl>
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">
          Operation (builtin)
        </h3>
        <p className="mt-1 text-xs text-text-tertiary">
          gRPC exposes a generic <span className="font-mono">call</span> op
          (unary) plus server-streaming subscriptions (below). Methods are
          discovered live via server reflection — operators do not declare them
          here.
        </p>
        <table className="mt-2 w-full text-sm" data-testid="grpc-builtin-ops">
          <thead>
            <tr className="text-left text-text-tertiary">
              <th className="py-1">Op</th>
              <th className="py-1">Args</th>
              <th className="py-1">Returns</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-border">
              <td className="font-mono py-1">call</td>
              <td className="font-mono text-xs py-1">
                method, request, metadata?, timeout_seconds?
              </td>
              <td className="font-mono text-xs py-1">{'{ ...response JSON }'}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">
          Streams
        </h3>
        <p className="mt-1 text-xs text-text-tertiary">
          A server-streaming RPC (1 request → N responses) opens a long-lived
          subscription that buffers each response message in a fixed-size ring
          (overflow policy <code>drop_oldest</code> only). Drain pulls buffered
          items; the drop counters tell you whether you are seeing the complete
          stream. Client-streaming and bidirectional RPCs are out of scope.
        </p>

        {err && (
          <p
            className="mt-2 rounded bg-error/10 px-3 py-2 text-xs text-error"
            data-testid="grpc-streams-error"
          >
            {err}
          </p>
        )}

        <table className="mt-3 w-full text-sm" data-testid="grpc-streams-table">
          <thead>
            <tr className="text-left text-text-tertiary">
              <th className="py-1">Stream</th>
              <th className="py-1">Status</th>
              <th className="py-1">Buffered</th>
              <th className="py-1">Dropped</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {streams.length === 0 && (
              <tr>
                <td colSpan={5} className="py-2 text-xs italic text-text-tertiary">
                  {loading ? 'Loading…' : 'No active streams.'}
                </td>
              </tr>
            )}
            {streams.map(s => (
              <tr key={s.subscription_id} className="border-t border-border">
                <td className="font-mono text-xs py-1">{s.subscription_id}</td>
                <td className="py-1">{s.status}</td>
                <td className="py-1">{s.buffer_depth}</td>
                <td className="py-1">{s.overflow_dropped}</td>
                <td className="py-1 text-right space-x-2">
                  <button
                    type="button"
                    onClick={() => drainDebug(s.subscription_id)}
                    disabled={loading}
                    className="text-xs text-accent hover:underline disabled:opacity-50"
                    data-testid={`grpc-drain-${s.subscription_id}`}
                  >
                    Drain
                  </button>
                  <button
                    type="button"
                    onClick={() => unsubscribe(s.subscription_id)}
                    disabled={loading}
                    className="text-xs text-error hover:underline disabled:opacity-50"
                    data-testid={`grpc-unsubscribe-${s.subscription_id}`}
                  >
                    Unsubscribe
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-3 space-y-2">
          <div>
            <label className="block text-xs text-text-tertiary">method</label>
            <input
              type="text"
              placeholder="package.Service/StreamMethod"
              value={method}
              onChange={e => setMethod(e.target.value)}
              data-testid="grpc-new-method"
              className="mt-1 w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
            />
          </div>
          <div>
            <label className="block text-xs text-text-tertiary">request (JSON)</label>
            <textarea
              rows={3}
              placeholder='{ "message": "hi" }'
              value={requestJson}
              onChange={e => setRequestJson(e.target.value)}
              data-testid="grpc-new-request"
              className="mt-1 w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
            />
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={addStream}
              disabled={loading || !method.trim()}
              className="rounded bg-accent px-3 py-1 text-xs text-text-on-accent disabled:opacity-50"
              data-testid="grpc-add-stream-button"
            >
              Subscribe
            </button>
          </div>
        </div>

        {drainOutput !== null && (
          <pre
            className="mt-3 max-h-48 overflow-auto rounded bg-bg-secondary px-3 py-2 text-xs font-mono"
            data-testid="grpc-drain-output"
          >
            {drainOutput}
          </pre>
        )}
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">Reflection</h3>
        <p className="mt-1 text-xs text-text-tertiary">
          The target server must have gRPC Server Reflection enabled — that is
          how method schemas are discovered (no <span className="font-mono">.proto</span>{' '}
          upload). Run Test to confirm reflection works; it lists the server&apos;s
          services. A reflection error almost always means reflection is disabled
          on the server.
        </p>
      </div>
    </div>
  );
}
