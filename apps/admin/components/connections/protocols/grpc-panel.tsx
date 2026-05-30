// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection, GrpcProtocolData } from '@/utils/connection-types';

export function GrpcPanel({ connection }: { connection: Connection }) {
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
          gRPC exposes a single generic <span className="font-mono">call</span> op.
          Methods are discovered live via server reflection — operators do not
          declare them here.
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
