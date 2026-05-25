// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).

/**
 * Unified connections API client. Replaces the legacy
 * adminApi.connections.providers/.tools/.oauth namespaces deleted in PR4.
 *
 * Mirrors PR4's generic CRUD routes at /api/v1/admin/connections/ + the
 * mounted plugin sub-routes at /api/v1/admin/connections/<plugin.id>/.
 *
 * The factory takes the platform's `analyticsClient` (a FetchClient that
 * targets the root host, NOT the /admin-prefixed base) because the unified
 * connections routes are mounted at absolute /api/v1/admin/connections/...
 */
import type { FetchClient } from './api-client';
import type {
  BackendMeta,
  Connection,
  ConnectionListParams,
  CreateConnectionPayload,
  OAuth2StartResponse,
  ProtocolMeta,
  TestConnectionResponse,
  UpdateConnectionPayload,
} from './connection-types';

const ROOT = '/api/v1/admin/connections';

function buildQuery(params?: ConnectionListParams): string {
  if (!params) return '';
  const qs = new URLSearchParams();
  if (params.protocol) qs.set('protocol', params.protocol);
  if (params.tag) qs.set('tag', params.tag);
  if (params.search) qs.set('search', params.search);
  const s = qs.toString();
  return s ? `?${s}` : '';
}

export function createConnectionsApi(client: FetchClient) {
  return {
    // Generic CRUD
    protocols: () => client.get<ProtocolMeta[]>(`${ROOT}/protocols`),
    backends: () => client.get<BackendMeta[]>(`${ROOT}/backends`),
    list: (params?: ConnectionListParams) =>
      client.get<Connection[]>(`${ROOT}/${buildQuery(params)}`),
    get: (id: string) =>
      client.get<Connection>(`${ROOT}/${encodeURIComponent(id)}`),
    create: (payload: CreateConnectionPayload) =>
      client.post<Connection>(`${ROOT}/`, payload),
    update: (id: string, payload: UpdateConnectionPayload) =>
      client.patch<Connection>(`${ROOT}/${encodeURIComponent(id)}`, payload),
    delete: (id: string) =>
      client.delete<void>(`${ROOT}/${encodeURIComponent(id)}`),
    test: (id: string) =>
      client.post<TestConnectionResponse>(
        `${ROOT}/${encodeURIComponent(id)}/test`,
        {},
      ),
    setDefault: (id: string) =>
      client.post<Connection>(
        `${ROOT}/${encodeURIComponent(id)}/set-default`,
        {},
      ),

    // OAuth2 plugin sub-routes (mounted at /api/v1/admin/connections/oauth2/...)
    oauth2: {
      start: (id: string) =>
        client.post<OAuth2StartResponse>(
          `${ROOT}/oauth2/${encodeURIComponent(id)}/start`,
          {},
        ),
      refresh: (id: string) =>
        client.post<Connection>(
          `${ROOT}/oauth2/${encodeURIComponent(id)}/refresh`,
          {},
        ),
      revoke: (id: string) =>
        client.post<Connection>(
          `${ROOT}/oauth2/${encodeURIComponent(id)}/revoke`,
          {},
        ),
    },
  };
}

export type ConnectionsApi = ReturnType<typeof createConnectionsApi>;

// Re-export types so consumers can import everything from one place.
export type {
  BackendMeta,
  Connection,
  ConnectionListParams,
  ConnectionStatus,
  CreateConnectionPayload,
  CredentialsBackendConfig,
  CredentialsBackendKind,
  OAuth2StartResponse,
  ProtocolMeta,
  TestConnectionResponse,
  UpdateConnectionPayload,
} from './connection-types';
