// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { CoapProtocolData, Connection } from '@/utils/connection-types';

const BUILTIN_OPS: Array<{ name: string; args: string; returns: string }> = [
  { name: 'get',    args: 'path, query?, accept?',                returns: '{code, payload, content_format}' },
  { name: 'post',   args: 'path, payload?, content_format?',      returns: '{code, payload, location?}' },
  { name: 'put',    args: 'path, payload, content_format?',       returns: '{code, payload}' },
  { name: 'delete', args: 'path',                                  returns: '{code}' },
];

export function CoapPanel({ connection }: { connection: Connection }) {
  const pd = connection.protocol_data as Partial<CoapProtocolData>;
  const baseUri = pd.base_uri ?? '';
  const isCoaps = baseUri.startsWith('coaps://');
  const pskIdentity = pd.psk_identity ?? '';
  const pskKey = pd.psk_key ?? '';
  const timeout = pd.default_timeout_seconds ?? 10;

  return (
    <div className="space-y-6" data-testid="coap-panel">
      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">Endpoint</h3>
        <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
          <dt className="text-text-tertiary">base_uri</dt>
          <dd className="col-span-2 font-mono">
            {baseUri || <em className="text-text-tertiary">not set</em>}
          </dd>
          <dt className="text-text-tertiary">DTLS</dt>
          <dd className="col-span-2">{isCoaps ? 'enabled (coaps://)' : 'off'}</dd>
          {isCoaps && (
            <>
              <dt className="text-text-tertiary">PSK identity</dt>
              <dd className="col-span-2 font-mono">
                {pskIdentity || <em className="text-text-tertiary">not set</em>}
              </dd>
              <dt className="text-text-tertiary">PSK key</dt>
              <dd className="col-span-2 font-mono">
                {pskKey === '***' ? '*** (encrypted)' : <em className="text-text-tertiary">not set</em>}
              </dd>
            </>
          )}
          <dt className="text-text-tertiary">timeout</dt>
          <dd className="col-span-2">{timeout}s</dd>
        </dl>
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">
          Operations (builtin)
        </h3>
        <p className="mt-1 text-xs text-text-tertiary">
          CoAP exposes four fixed verbs. Operators do not declare custom operations.
        </p>
        <table className="mt-2 w-full text-sm" data-testid="coap-builtin-ops">
          <thead>
            <tr className="text-left text-text-tertiary">
              <th className="py-1">Op</th>
              <th className="py-1">Args</th>
              <th className="py-1">Returns</th>
            </tr>
          </thead>
          <tbody>
            {BUILTIN_OPS.map(op => (
              <tr key={op.name} className="border-t border-border">
                <td className="font-mono py-1">{op.name}</td>
                <td className="font-mono text-xs py-1">{op.args}</td>
                <td className="font-mono text-xs py-1">{op.returns}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
