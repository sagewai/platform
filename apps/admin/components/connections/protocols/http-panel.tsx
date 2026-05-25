// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection } from '@/utils/connection-types';

type HttpProtocolData = {
  base_url: string;
  auth: { kind: string; header?: string; prefix?: string };
  runtime_base_url_field?: string;
  operations_ref?: string;
  operations?: Record<string, { method: string; path: string }>;
};

export function HttpPanel({ connection }: { connection: Connection }) {
  const pd = connection.protocol_data as HttpProtocolData;
  const opsCount = pd.operations_ref
    ? `via catalog: ${pd.operations_ref}`
    : `${Object.keys(pd.operations ?? {}).length} ops inline`;
  return (
    <dl className="space-y-2 text-sm" data-testid="http-panel">
      <div>
        <dt className="text-text-secondary">Base URL</dt>
        <dd className="font-mono">{pd.base_url}</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Auth kind</dt>
        <dd>{pd.auth.kind}</dd>
      </div>
      {pd.runtime_base_url_field && (
        <div>
          <dt className="text-text-secondary">Runtime base URL field</dt>
          <dd className="font-mono">{pd.runtime_base_url_field}</dd>
        </div>
      )}
      <div>
        <dt className="text-text-secondary">Operations</dt>
        <dd>{opsCount}</dd>
      </div>
    </dl>
  );
}
