// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection } from '@/utils/connection-types';
import { BackendPill } from './backend-pill';
import { StatusPill } from './status-pill';

type Props = {
  connections: Connection[];
  protocolNames: Record<string, string>; // {id → display_name} from /protocols
  onRowClick: (c: Connection) => void;
  onSetDefault: (c: Connection) => void;
  onAction: (
    c: Connection,
    action: 'test' | 'delete' | 'authorize' | 'refresh' | 'revoke',
  ) => void;
};

function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  const diffSec = (Date.now() - t) / 1000;
  if (diffSec < 60) return `${Math.floor(diffSec)}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

export function ConnectionsTable({
  connections, protocolNames, onRowClick, onSetDefault, onAction,
}: Props) {
  if (connections.length === 0) {
    return (
      <div
        className="py-12 text-center text-text-tertiary"
        data-testid="connections-empty"
      >
        No connections match these filters.
      </div>
    );
  }

  return (
    <table className="w-full text-sm" data-testid="connections-table">
      <thead className="border-b border-border text-left text-text-secondary">
        <tr>
          <th className="px-2 py-2">Protocol</th>
          <th className="px-2 py-2">Display name</th>
          <th className="px-2 py-2">Tags</th>
          <th className="px-2 py-2">Status</th>
          <th className="px-2 py-2">Backend</th>
          <th className="px-2 py-2">Default</th>
          <th className="px-2 py-2">Last tested</th>
          <th className="w-12 px-2 py-2">Actions</th>
        </tr>
      </thead>
      <tbody>
        {connections.map(c => (
          <tr
            key={c.id}
            onClick={() => onRowClick(c)}
            className="cursor-pointer border-b border-border-muted hover:bg-bg-subtle"
            data-testid={`connection-row-${c.id}`}
          >
            <td className="px-2 py-2 text-text-secondary">
              {protocolNames[c.protocol] ?? c.protocol}
            </td>
            <td className="px-2 py-2 font-medium">{c.display_name}</td>
            <td className="px-2 py-2">
              {c.tags.map(t => (
                <span
                  key={t}
                  className="mr-1 inline-block rounded bg-bg-subtle px-1.5 py-0.5 text-xs text-text-secondary"
                >
                  {t}
                </span>
              ))}
            </td>
            <td className="px-2 py-2"><StatusPill status={c.status} /></td>
            <td className="px-2 py-2">
              <BackendPill kind={c.credentials_backend?.kind ?? null} />
            </td>
            <td className="px-2 py-2">
              <button
                type="button"
                onClick={e => { e.stopPropagation(); onSetDefault(c); }}
                className="text-lg"
                data-testid={`default-toggle-${c.id}`}
                aria-label={
                  c.is_default
                    ? 'default (click to set another)'
                    : 'click to set as default'
                }
              >
                {c.is_default ? '★' : '☆'}
              </button>
            </td>
            <td className="px-2 py-2 text-text-tertiary">
              {relativeTime(c.last_tested_at)}
            </td>
            <td className="px-2 py-2" onClick={e => e.stopPropagation()}>
              <details className="relative">
                <summary
                  className="cursor-pointer list-none px-1 hover:text-text-primary"
                  aria-label="row actions"
                >
                  ⋯
                </summary>
                <ul className="absolute right-0 z-10 mt-1 w-36 rounded border border-border bg-bg py-1 shadow-lg">
                  <li>
                    <button
                      className="w-full px-3 py-1 text-left hover:bg-bg-subtle"
                      onClick={() => onAction(c, 'test')}
                    >
                      Test
                    </button>
                  </li>
                  {c.protocol === 'oauth2' && c.status === 'pending' && (
                    <li>
                      <button
                        className="w-full px-3 py-1 text-left hover:bg-bg-subtle"
                        onClick={() => onAction(c, 'authorize')}
                      >
                        Authorize
                      </button>
                    </li>
                  )}
                  {c.protocol === 'oauth2' && c.status !== 'pending' && (
                    <li>
                      <button
                        className="w-full px-3 py-1 text-left hover:bg-bg-subtle"
                        onClick={() => onAction(c, 'authorize')}
                      >
                        Re-authorize
                      </button>
                    </li>
                  )}
                  {c.protocol === 'oauth2' && c.status === 'ready' && (
                    <li>
                      <button
                        className="w-full px-3 py-1 text-left hover:bg-bg-subtle"
                        onClick={() => onAction(c, 'refresh')}
                      >
                        Refresh tokens
                      </button>
                    </li>
                  )}
                  {c.protocol === 'oauth2'
                    && (c.status === 'ready' || c.status === 'expired') && (
                      <li>
                        <button
                          className="w-full px-3 py-1 text-left hover:bg-bg-subtle"
                          onClick={() => onAction(c, 'revoke')}
                        >
                          Revoke
                        </button>
                      </li>
                  )}
                  <li>
                    <button
                      className="w-full px-3 py-1 text-left text-error hover:bg-bg-subtle"
                      onClick={() => onAction(c, 'delete')}
                    >
                      Delete
                    </button>
                  </li>
                </ul>
              </details>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
