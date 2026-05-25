// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection } from '@/utils/connection-types';

type SdkProtocolData = {
  entrypoint: string;
  credential_fields: {
    name: string;
    label: string;
    type: 'password' | 'text';
    description?: string;
  }[];
  secrets?: Record<string, string>;
};

export function SdkPanel({ connection }: { connection: Connection }) {
  const pd = connection.protocol_data as SdkProtocolData;
  return (
    <dl className="space-y-2 text-sm" data-testid="sdk-panel">
      <div>
        <dt className="text-text-secondary">Entrypoint</dt>
        <dd className="font-mono text-xs">{pd.entrypoint}</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Credential fields</dt>
        <dd>
          <ul className="space-y-1">
            {(pd.credential_fields ?? []).map(f => (
              <li key={f.name} className="text-xs">
                <span className="font-mono">{f.name}</span> ({f.label}, {f.type})
                {pd.secrets?.[f.name] !== undefined && (
                  <span className="ml-2 text-text-tertiary">
                    = {pd.secrets[f.name] === '***'
                      ? '•••••••• (masked)'
                      : pd.secrets[f.name]}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </dd>
      </div>
    </dl>
  );
}
