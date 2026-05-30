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
  ExportParams,
  GrpcStream,
  ImportParams,
  ImportResult,
  McpServerMeta,
  McpToolsResponse,
  MqttDrainResult,
  MqttSubscription,
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

    // Import / export (YAML)
    exportYaml: async (params: ExportParams): Promise<Blob> => {
      const search = new URLSearchParams();
      if (params.project_id) search.set('project_id', params.project_id);
      if (params.secrets) search.set('secrets', params.secrets);
      if (params.include_id) search.set('include_id', 'true');
      for (const p of params.protocols || []) search.append('protocol', p);
      for (const t of params.tags || []) search.append('tag', t);

      const resp = await fetch(
        `${ROOT}/export?${search.toString()}`,
        { credentials: 'include' },
      );
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`export failed: ${resp.status} ${text}`);
      }
      return resp.blob();
    },

    importYaml: async (
      file: File,
      params: ImportParams,
    ): Promise<ImportResult> => {
      const search = new URLSearchParams();
      if (params.project_id) search.set('project_id', params.project_id);
      if (params.mode) search.set('mode', params.mode);
      if (params.dry_run) search.set('dry_run', 'true');
      if (params.preserve_ids) search.set('preserve_ids', 'true');

      const form = new FormData();
      form.append('file', file);

      const resp = await fetch(
        `${ROOT}/import?${search.toString()}`,
        {
          method: 'POST',
          credentials: 'include',
          body: form,
        },
      );
      // Both 200 (with errors[]) and 400 (parse/version error) return JSON
      // bodies with the ImportResult shape.
      const data = (await resp.json()) as ImportResult;
      return data;
    },

    // MCP plugin sub-routes (mounted at /api/v1/admin/connections/mcp/...)
    mcp: {
      servers: () => client.get<McpServerMeta[]>(`${ROOT}/mcp/servers`),
      refresh: (id: string) =>
        client.post<Record<string, unknown>>(
          `${ROOT}/mcp/${encodeURIComponent(id)}/refresh`,
          {},
        ),
      listTools: (id: string) =>
        client.get<McpToolsResponse>(
          `${ROOT}/mcp/${encodeURIComponent(id)}/tools`,
        ),
    },

    // MQTT plugin sub-routes (mounted at /api/v1/admin/connections/mqtt/...).
    // MQTT is the first STATEFUL kind: subscriptions are long-lived buffers
    // owned by the admin-process SubscriptionManager. PR2 ships drop_oldest
    // only.
    mqtt: {
      subscribe: (id: string, spec: { topic_filter: string; qos?: number }) =>
        client.post<{ subscription_id: string }>(
          `${ROOT}/mqtt/${encodeURIComponent(id)}/subscribe`,
          spec,
        ),
      listSubscriptions: () =>
        client.get<MqttSubscription[]>(`${ROOT}/mqtt/subscriptions`),
      stats: (subId: string) =>
        client.get<MqttSubscription>(
          `${ROOT}/mqtt/subscriptions/${encodeURIComponent(subId)}`,
        ),
      drain: (subId: string, maxEvents = 100) =>
        client.post<MqttDrainResult>(
          `${ROOT}/mqtt/subscriptions/${encodeURIComponent(subId)}/drain`,
          { max_events: maxEvents },
        ),
      unsubscribe: (subId: string) =>
        client.delete<void>(
          `${ROOT}/mqtt/subscriptions/${encodeURIComponent(subId)}`,
        ),
    },

    // gRPC server-streaming sub-routes (mounted at
    // /api/v1/admin/connections/grpc/...). gRPC is dual-mode: the unary
    // `call` op is stateless; server-streaming subscriptions are long-lived
    // buffers owned by the admin-process SubscriptionManager (same machinery
    // as MQTT). drop_oldest only.
    grpc: {
      subscribe: (
        id: string,
        spec: { method: string; request?: Record<string, unknown>; metadata?: Record<string, string> },
      ) =>
        client.post<{ subscription_id: string }>(
          `${ROOT}/grpc/${encodeURIComponent(id)}/subscribe`,
          spec,
        ),
      listSubscriptions: () =>
        client.get<GrpcStream[]>(`${ROOT}/grpc/subscriptions`),
      stats: (subId: string) =>
        client.get<GrpcStream>(
          `${ROOT}/grpc/subscriptions/${encodeURIComponent(subId)}`,
        ),
      drain: (subId: string, maxEvents = 100) =>
        client.post<MqttDrainResult>(
          `${ROOT}/grpc/subscriptions/${encodeURIComponent(subId)}/drain`,
          { max_events: maxEvents },
        ),
      unsubscribe: (subId: string) =>
        client.delete<void>(
          `${ROOT}/grpc/subscriptions/${encodeURIComponent(subId)}`,
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
  GrpcStream,
  McpCredentialFieldMeta,
  McpServerMeta,
  McpToolMeta,
  McpToolsResponse,
  MqttDrainResult,
  MqttProtocolData,
  MqttSubscription,
  OAuth2StartResponse,
  ProtocolMeta,
  TestConnectionResponse,
  UpdateConnectionPayload,
} from './connection-types';
