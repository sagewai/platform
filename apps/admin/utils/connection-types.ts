// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).

/**
 * Unified connection types — mirror sagewai.connections (PR1) + plugin
 * (PR2) + credentials backend (PR3) shapes shipped to operators via
 * the generic admin routes (PR4).
 */

export type ConnectionStatus =
  | 'ready'
  | 'pending'
  | 'expired'
  | 'revoked'
  | 'error';

export type CredentialsBackendKind = 'local' | 'env' | 'sops' | 'vault' | 'doppler';

export type CredentialsBackendConfig = {
  kind: CredentialsBackendKind;
  config: Record<string, unknown>;
};

export type VaultBackendConfig = {
  url: string;
  namespace?: string | null;
  mount?: string;
  base_path: string;
  auth:
    | { mode: 'token'; token: string }
    | { mode: 'approle'; role_id: string; secret_id: string };
  verify_tls?: boolean;
};

export type DopplerBackendConfig = {
  service_token: string;
  project: string;
  config: string;
  name_prefix: string;
  base_url?: string;
};

export type ProtocolMeta = {
  id: string; // "http" | "oauth2" | "mcp" | "inference" | "sdk"
  display_name: string;
  sensitive_fields: string[];
};

export type BackendMeta = {
  id: CredentialsBackendKind;
  display_name: string;
};

/**
 * Connection record as returned by the admin API.
 * `protocol_data` is masked per plugin.public_view() — sensitive fields
 * (e.g., `client_secret`, `tokens.access_token`) come back as "***".
 */
export type Connection = {
  id: string;
  kind: 'connection';
  protocol: string;
  project_id: string | null;
  display_name: string;
  tags: string[];
  credentials_backend: CredentialsBackendConfig | null;
  status: ConnectionStatus;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  last_error: { code: string; message: string; at: string } | null;
  // Plugin-owned blob; shape varies per protocol. Components that need
  // to render protocol-specific fields cast this via narrowed types in
  // the protocol panel modules.
  protocol_data: Record<string, unknown>;
};

export type ConnectionListParams = {
  protocol?: string;
  tag?: string;
  search?: string;
};

export type CreateConnectionPayload = {
  protocol: string;
  display_name: string;
  tags: string[];
  credentials_backend?: CredentialsBackendConfig | null;
  protocol_data: Record<string, unknown>;
};

export type UpdateConnectionPayload = Partial<{
  display_name: string;
  tags: string[];
  credentials_backend: CredentialsBackendConfig | null;
  protocol_data: Record<string, unknown>;
  status: ConnectionStatus;
}>;

export type TestConnectionResponse = {
  ok: boolean;
  status_code: number | null;
  message: string | null;
};

// OAuth2 sub-route response shapes

export type OAuth2StartResponse = {
  authorize_url: string;
  state: string;
};

// MCP sub-route response shapes

export type McpCredentialFieldMeta = {
  name: string;
  label: string;
  type: 'password' | 'text';
  injection: 'env' | 'header';
  description: string | null;
};

export type McpServerMeta = {
  id: string;
  display_name: string;
  transport: 'stdio' | 'http' | 'sse';
  default_command: string[] | null;
  default_args: string[] | null;
  credential_fields: McpCredentialFieldMeta[];
  docs_url: string;
  description: string;
};

export type McpToolMeta = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
};

export type McpToolsResponse = {
  tools: McpToolMeta[];
  last_discovered_at: string | null;
};

// CoAP protocol-data shape (RFC 7252) — Phase A PR1.
export type CoapProtocolData = {
  base_uri: string;
  use_dtls: boolean;
  psk_identity: string;
  psk_key: string; // server returns '***' when populated
  default_timeout_seconds: number;
  sandbox_tier_override: 'TRUSTED' | 'SANDBOXED' | null;
};

// Modbus/TCP protocol-data shape (Modbus Application Protocol V1.1b3) — Phase A PR2.
export type ModbusProtocolData = {
  host: string;
  port: number;
  transport: 'tcp';
  unit_id: number;
  default_timeout_seconds: number;
  sandbox_tier_override: 'TRUSTED' | 'SANDBOXED' | 'UNTRUSTED' | null;
};

// OPC UA (IEC 62541) protocol-data shape — Phase A PR3.
export type OpcuaOperation = {
  name: string;
  kind: 'read';
  node_id: string;
};

export type OpcuaProtocolData = {
  endpoint_url: string;
  security_mode: 'None' | 'Sign' | 'SignAndEncrypt';
  security_policy:
    | 'None'
    | 'Basic256Sha256'
    | 'Basic256'
    | 'Basic128Rsa15'
    | 'Aes128_Sha256_RsaOaep'
    | 'Aes256_Sha256_RsaPss';
  auth_mode: 'anonymous' | 'username';
  username: string;
  password: string; // masked '***' from server responses
  operations: OpcuaOperation[];
  sandbox_tier_override: 'TRUSTED' | 'SANDBOXED' | null;
};

// Connection import/export — Phase 1.

export interface ImportEntry {
  id: string;
  protocol: string;
  display_name: string;
}

export interface ImportErrorEntry {
  row_index: number;
  protocol: string;
  display_name: string;
  code: string;
  message: string;
}

export interface ImportResult {
  dry_run: boolean;
  created: ImportEntry[];
  updated: ImportEntry[];
  skipped: ImportEntry[];
  errors: ImportErrorEntry[];
}

export type SecretsMode = 'redacted' | 'encrypted' | 'placeholder';
export type ConflictMode = 'create-only' | 'upsert' | 'skip-existing';

export interface ExportParams {
  project_id?: string;
  secrets?: SecretsMode;
  protocols?: string[];
  tags?: string[];
  include_id?: boolean;
}

export interface ImportParams {
  project_id?: string;
  mode?: ConflictMode;
  dry_run?: boolean;
  preserve_ids?: boolean;
}

// WebSocket (RFC 6455) protocol-data shape — Phase A PR4.
export type WebsocketOperation = {
  name: string;
  message_template: string;
  response_match?: string | null;
  timeout_seconds?: number | null;
};

export type WebsocketProtocolData = {
  url: string;
  headers: Record<string, string>;
  auth_header_name: string;
  auth_header_value: string; // masked '***' from server responses
  default_timeout_seconds: number;
  operations: WebsocketOperation[];
  sandbox_tier_override: 'TRUSTED' | 'SANDBOXED' | null;
};
