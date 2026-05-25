// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection } from '@/utils/connection-types';

type Oauth2ProtocolData = {
  provider: string;
  client_id: string;
  client_secret: string; // masked
  redirect_uri: string;
  requested_scopes: string[];
  granted_scopes: string[];
  tokens: {
    access_token: string; // masked
    refresh_token: string | null; // masked if present
    token_type: string;
    expires_at: string;
    obtained_at: string;
    last_refreshed_at: string | null;
  } | null;
};

export function Oauth2Panel({ connection }: { connection: Connection }) {
  const pd = connection.protocol_data as Oauth2ProtocolData;
  const missing = pd.requested_scopes.filter(s => !pd.granted_scopes.includes(s));
  return (
    <dl className="space-y-2 text-sm" data-testid="oauth2-panel">
      <div>
        <dt className="text-text-secondary">Provider</dt>
        <dd>{pd.provider}</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Client ID</dt>
        <dd className="font-mono">{pd.client_id}</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Client secret</dt>
        <dd className="font-mono text-text-tertiary">•••••••• (masked)</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Redirect URI</dt>
        <dd className="break-all font-mono text-xs">{pd.redirect_uri}</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Requested scopes</dt>
        <dd>{pd.requested_scopes.join(', ')}</dd>
      </div>
      <div>
        <dt className="text-text-secondary">Granted scopes</dt>
        <dd>
          {pd.granted_scopes.length === 0
            ? <span className="text-text-tertiary">none yet</span>
            : pd.granted_scopes.join(', ')}
          {missing.length > 0 && (
            <div className="mt-1 text-xs text-warning">
              Missing: {missing.join(', ')}
            </div>
          )}
        </dd>
      </div>
      {pd.tokens && (
        <>
          <div>
            <dt className="text-text-secondary">Token expires at</dt>
            <dd>{pd.tokens.expires_at}</dd>
          </div>
          <div>
            <dt className="text-text-secondary">Last refreshed</dt>
            <dd>{pd.tokens.last_refreshed_at ?? 'never'}</dd>
          </div>
        </>
      )}
    </dl>
  );
}
