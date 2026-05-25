// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection } from '@/utils/connection-types';

type McpProtocolData = {
  transport: 'stdio' | 'http' | 'sse';
  command?: string[];
  args?: string[];
  url?: string;
};

export function McpPanel({ connection }: { connection: Connection }) {
  const pd = connection.protocol_data as McpProtocolData;
  return (
    <dl className="space-y-2 text-sm" data-testid="mcp-panel">
      <div>
        <dt className="text-text-secondary">Transport</dt>
        <dd>{pd.transport}</dd>
      </div>
      {pd.transport === 'stdio' && pd.command && (
        <>
          <div>
            <dt className="text-text-secondary">Command</dt>
            <dd className="font-mono">{pd.command.join(' ')}</dd>
          </div>
          {pd.args && pd.args.length > 0 && (
            <div>
              <dt className="text-text-secondary">Args</dt>
              <dd className="font-mono">{pd.args.join(' ')}</dd>
            </div>
          )}
        </>
      )}
      {pd.transport !== 'stdio' && pd.url && (
        <div>
          <dt className="text-text-secondary">URL</dt>
          <dd className="break-all font-mono text-xs">{pd.url}</dd>
        </div>
      )}
    </dl>
  );
}
