/** OAuth client / provider types for the admin connections OAuth tab. */

export type OAuthProviderMeta = {
  id: string;
  display_name: string;
  default_scopes: string[];
  docs_url: string;
};

export type OAuthClientStatus =
  | 'pending'
  | 'authorized'
  | 'expired'
  | 'revoked'
  | 'error';

export type OAuthClientTokensMeta = {
  token_type: string;
  expires_at: string;
  obtained_at: string;
  last_refreshed_at: string | null;
} | null;

export type OAuthClient = {
  id: string;
  kind: 'oauth_client';
  project_id: string;
  provider: string;
  display_name: string;
  redirect_uri: string;
  requested_scopes: string[];
  granted_scopes: string[];
  tokens: OAuthClientTokensMeta;
  is_default: boolean;
  status: OAuthClientStatus;
  last_error: { code: string; message: string; at: string } | null;
  created_at: string;
  updated_at: string;
};

export type OAuthCreatePayload = {
  provider: string;
  display_name: string;
  client_id: string;
  client_secret: string;
  requested_scopes: string[];
};

export type OAuthCreateResponse = {
  record: OAuthClient;
  authorize_url: string;
  state: string;
};

export type OAuthStartResponse = {
  authorize_url: string;
  state: string;
};
