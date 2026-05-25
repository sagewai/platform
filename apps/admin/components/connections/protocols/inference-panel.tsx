// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection } from '@/utils/connection-types';

type InferenceProtocolData = {
  provider_key: string;
  base_url?: string;
  model_name?: string;
  secrets: Record<string, string>; // values masked
};

export function InferencePanel({ connection }: { connection: Connection }) {
  const pd = connection.protocol_data as InferenceProtocolData;
  return (
    <dl className="space-y-2 text-sm" data-testid="inference-panel">
      <div>
        <dt className="text-text-secondary">Provider key</dt>
        <dd>{pd.provider_key}</dd>
      </div>
      {pd.base_url && (
        <div>
          <dt className="text-text-secondary">Base URL</dt>
          <dd className="break-all font-mono text-xs">{pd.base_url}</dd>
        </div>
      )}
      {pd.model_name && (
        <div>
          <dt className="text-text-secondary">Model</dt>
          <dd>{pd.model_name}</dd>
        </div>
      )}
      <div>
        <dt className="text-text-secondary">Secrets</dt>
        <dd>
          {Object.keys(pd.secrets ?? {}).length === 0 ? (
            <span className="text-text-tertiary">none</span>
          ) : (
            <ul className="space-y-0.5 font-mono text-xs">
              {Object.keys(pd.secrets).map(k => (
                <li key={k}>
                  {k} = <span className="text-text-tertiary">•••••••• (masked)</span>
                </li>
              ))}
            </ul>
          )}
        </dd>
      </div>
    </dl>
  );
}
