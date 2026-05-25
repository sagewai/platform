// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useState } from 'react';
import type { Connection, McpToolMeta } from '@/utils/connection-types';
import { adminApi } from '@/utils/api';

type McpProtocolData = {
  server_ref?: string;
  transport: 'stdio' | 'http' | 'sse';
  command?: string[];
  args?: string[];
  url?: string;
  credentials?: Record<string, string>; // values masked when sensitive
  discovered_tools?: McpToolMeta[];
  last_discovered_at?: string | null;
};

type Props = {
  connection: Connection;
  onRefresh?: () => void | Promise<void>;
};

export function McpPanel({ connection, onRefresh }: Props) {
  const pd = connection.protocol_data as McpProtocolData;
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const handleRefresh = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await adminApi.connections.mcp.refresh(connection.id);
      if (onRefresh) await onRefresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setRefreshError(msg);
    } finally {
      setRefreshing(false);
    }
  };

  const tools = pd.discovered_tools ?? [];
  const credentials = pd.credentials ?? {};
  const hasCredentials = Object.keys(credentials).length > 0;

  return (
    <div className="space-y-4" data-testid="mcp-panel">
      {pd.server_ref && (
        <div>
          <span className="rounded bg-bg-subtle px-1.5 py-0.5 text-xs font-mono text-text-secondary">
            {pd.server_ref}
          </span>
        </div>
      )}

      <dl className="space-y-2 text-sm">
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
        {hasCredentials && (
          <div>
            <dt className="text-text-secondary">Credentials</dt>
            <dd>
              <ul className="space-y-0.5">
                {Object.entries(credentials).map(([k, v]) => (
                  <li key={k} className="font-mono text-xs">
                    {k}:{' '}
                    {v === '***' ? (
                      <span className="text-text-tertiary">
                        ••••••• (masked)
                      </span>
                    ) : (
                      v
                    )}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
      </dl>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <h4 className="text-sm font-medium">
            Discovered tools ({tools.length})
          </h4>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded border border-border px-2 py-0.5 text-xs hover:bg-bg-subtle disabled:opacity-50"
            data-testid="mcp-refresh-button"
          >
            {refreshing ? 'Refreshing…' : 'Refresh tools'}
          </button>
        </div>
        {pd.last_discovered_at && (
          <p className="text-xs text-text-tertiary">
            Last discovered: {new Date(pd.last_discovered_at).toLocaleString()}
          </p>
        )}
        {refreshError && (
          <p className="mt-1 text-xs text-error">{refreshError}</p>
        )}
        {tools.length === 0 ? (
          <p className="mt-1 text-xs text-text-tertiary">
            No tools discovered yet. Click Refresh to probe the server.
          </p>
        ) : (
          <ul className="mt-2 space-y-1" data-testid="mcp-tools-list">
            {tools.map(t => (
              <li key={t.name} className="text-xs">
                <span className="font-mono">{t.name}</span>
                {t.description && (
                  <span className="text-text-secondary"> — {t.description}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
