'use client';

import { useEffect, useState, use, useCallback } from 'react';
import Link from 'next/link';
import { Card, Button, Badge, FormField, TextInput, Tabs, Skeleton, useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import { ConnectorHealthBadge } from '@/components/connector-health-badge';
import type { ConnectorCatalogItem, ConnectorHealthResult, ConnectorTool } from '@/utils/types';
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Plug,
  Radio,
  RefreshCw,
  Sparkles,
  MessageSquare,
  Webhook,
  Wrench,
} from 'lucide-react';

interface Props {
  params: Promise<{ name: string }>;
}

export default function ConnectorDetailPage({ params }: Props) {
  const { name: rawName } = use(params);
  const name = decodeURIComponent(rawName);
  const { toast } = useToast();

  const [connector, setConnector] = useState<ConnectorCatalogItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');

  /* ── Health ── */
  const [healthResult, setHealthResult] = useState<ConnectorHealthResult | null>(null);
  const [testing, setTesting] = useState(false);

  /* ── Credentials ── */
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  /* ── Tools ── */
  const [tools, setTools] = useState<ConnectorTool[] | null>(null);
  const [discovering, setDiscovering] = useState(false);

  /* ── Tool schema expand/collapse ── */
  const [expandedTool, setExpandedTool] = useState<string | null>(null);

  /* ── Load connector ── */
  const loadConnector = useCallback(async () => {
    try {
      const all = await adminApi.listConnectors();
      const found = all.find((c) => c.name === name);
      if (found) {
        setConnector(found);
        const init: Record<string, string> = {};
        for (const f of found.auth_fields || []) init[f.key] = '';
        setFieldValues(init);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => { loadConnector(); }, [loadConnector]);

  /* ── Handlers ── */
  const handleTest = useCallback(async () => {
    setTesting(true);
    try {
      const result = await adminApi.testConnector(name);
      setHealthResult(result);
      if (result.status === 'healthy') {
        toast('success', `Healthy${result.latency_ms ? ` (${result.latency_ms}ms)` : ''} — ${result.tool_count || 0} tools`);
      } else {
        toast('error', result.error || `Status: ${result.status}`);
      }
    } catch {
      toast('error', 'Test failed');
    } finally {
      setTesting(false);
    }
  }, [name, toast]);

  /* ── Auto-test on load if connected ── */
  useEffect(() => {
    if (connector && (connector.connected || connector.auth_type === 'none')) {
      handleTest();
    }
  }, [connector?.name, connector?.connected, connector?.auth_type, handleTest]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await adminApi.saveConnectorCredentials(name, fieldValues);
      toast('success', 'Credentials saved');
      await loadConnector();
    } catch {
      toast('error', 'Failed to save credentials');
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    try {
      await adminApi.deleteConnector(name);
      toast('success', 'Disconnected');
      setHealthResult(null);
      setTools(null);
      await loadConnector();
    } catch {
      toast('error', 'Failed to disconnect');
    }
  };

  const handleDiscoverTools = async () => {
    setDiscovering(true);
    try {
      const result = await adminApi.discoverConnectorTools(name);
      setTools(result.tools || []);
      toast('success', `${result.count || 0} tools discovered`);
    } catch {
      toast('error', 'Failed to discover tools — is the MCP server reachable?');
    } finally {
      setDiscovering(false);
    }
  };

  const updateField = (key: string, value: string) => {
    setFieldValues((prev) => ({ ...prev, [key]: value }));
  };

  const allFieldsFilled = connector
    ? (connector.auth_fields || []).every((f) => fieldValues[f.key]?.trim())
    : false;

  /* ── Loading ── */
  if (loading) {
    return (
      <div className="max-w-6xl mx-auto">
        <div className="flex flex-col gap-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  if (!connector) {
    return (
      <div className="max-w-6xl mx-auto">
        <Card>
          <div className="text-center py-8 text-text-muted">
            <Plug size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">Connector &ldquo;{name}&rdquo; not found in the catalog.</p>
            <Link href="/settings/services" className="text-primary text-sm mt-2 inline-block">
              Back to Connectors
            </Link>
          </div>
        </Card>
      </div>
    );
  }

  /* ── Tab: Overview ── */
  const overviewTab = (
    <div className="flex flex-col gap-4">
      {/* Metadata card */}
      <Card>
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold m-0">{connector.display_name}</h3>
            <ConnectorHealthBadge connected={connector.connected} health={healthResult} />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Name</div>
              <div className="text-sm font-[family-name:var(--font-mono)] text-text-primary">{connector.name}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Category</div>
              <div className="text-sm text-text-primary capitalize">{connector.category}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Auth Type</div>
              <Badge variant="info" className="text-[10px]">
                {connector.auth_type === 'api_key' ? 'API Key' : connector.auth_type === 'oauth2' ? 'OAuth 2.0' : connector.auth_type === 'env_key' ? 'ENV Key' : 'No Auth'}
              </Badge>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Type</div>
              {connector.is_custom
                ? <Badge variant="warning" className="text-[10px]">Custom</Badge>
                : <Badge variant="default" className="text-[10px]">Builtin</Badge>
              }
            </div>
          </div>

          {connector.description && (
            <p className="text-sm text-text-secondary m-0">{connector.description}</p>
          )}

          {/* Capabilities */}
          <div className="flex items-center gap-3">
            {connector.supports_webhook && (
              <span className="flex items-center gap-1 text-xs text-text-muted">
                <Webhook size={12} /> Webhooks
              </span>
            )}
            {connector.supports_listener && (
              <span className="flex items-center gap-1 text-xs text-text-muted">
                <Radio size={12} /> Listeners
              </span>
            )}
            {connector.supports_poller && (
              <span className="flex items-center gap-1 text-xs text-text-muted">
                <RefreshCw size={12} /> Polling
              </span>
            )}
            {connector.docs_url && (
              <a
                href={connector.docs_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-text-muted hover:text-primary flex items-center gap-1 ml-auto transition-colors"
              >
                Documentation <ExternalLink size={11} />
              </a>
            )}
          </div>
        </div>
      </Card>

      {/* Agent description */}
      {connector.agent_description && (
        <div className="text-xs text-text-secondary bg-bg-subtle rounded-md px-3 py-2 border border-border">
          <div className="flex items-center gap-1.5 text-text-muted font-medium mb-1">
            <Sparkles size={12} className="text-primary" />
            When to use
          </div>
          {connector.agent_description}
        </div>
      )}

      {/* Example prompt */}
      {connector.example_prompt && (
        <div className="text-xs bg-primary/5 border border-primary/15 rounded-md px-3 py-2">
          <div className="flex items-center justify-between mb-1">
            <span className="text-text-muted font-medium flex items-center gap-1.5">
              <MessageSquare size={12} className="text-primary" />
              Example prompt
            </span>
            <button
              type="button"
              onClick={() => { navigator.clipboard?.writeText(connector.example_prompt)?.catch(() => {}); }}
              className="text-text-muted hover:text-primary bg-transparent border-none cursor-pointer p-0.5 transition-colors"
              title="Copy prompt"
            >
              <Copy size={11} />
            </button>
          </div>
          <p className="m-0 text-text-primary italic leading-relaxed">
            &ldquo;{connector.example_prompt}&rdquo;
          </p>
        </div>
      )}

      {/* Health details */}
      {healthResult && (
        <Card>
          <h4 className="text-sm font-semibold m-0 mb-3">Health Status</h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Status</div>
              <ConnectorHealthBadge connected={connector.connected} health={healthResult} showLatency={false} />
            </div>
            {healthResult.latency_ms != null && (
              <div>
                <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Latency</div>
                <div className="text-sm text-text-primary font-[family-name:var(--font-mono)]">{healthResult.latency_ms}ms</div>
              </div>
            )}
            {healthResult.tool_count != null && (
              <div>
                <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Tools</div>
                <div className="text-sm text-text-primary">{healthResult.tool_count}</div>
              </div>
            )}
            {healthResult.last_check && (
              <div>
                <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Last Check</div>
                <div className="text-xs text-text-secondary">{new Date(healthResult.last_check).toLocaleString()}</div>
              </div>
            )}
          </div>
          {healthResult.error && (
            <div className="mt-3 text-xs bg-error/10 text-error rounded-md px-3 py-2">
              {healthResult.error}
            </div>
          )}
        </Card>
      )}
    </div>
  );

  /* ── Tab: Configuration ── */
  const configTab = (
    <div className="flex flex-col gap-4">
      {/* Credential form */}
      {connector.auth_type !== 'none' && !connector.connected && (connector.auth_fields || []).length > 0 && (
        <Card>
          <h4 className="text-sm font-semibold m-0 mb-3">Credentials</h4>
          <form autoComplete="off" onSubmit={(e) => e.preventDefault()} className="flex flex-col gap-3">
            <input type="text" className="hidden" tabIndex={-1} aria-hidden="true" readOnly />
            <input type="password" className="hidden" tabIndex={-1} aria-hidden="true" readOnly />
            {connector.auth_fields.map((field) => (
              <FormField
                key={field.key}
                label={field.label}
                hint={
                  field.env_var
                    ? `Or set ${field.env_var} in your .env file${field.hint ? ` — ${field.hint}` : ''}`
                    : field.hint || undefined
                }
              >
                <TextInput
                  type={field.secret ? 'password' : 'text'}
                  value={fieldValues[field.key] || ''}
                  onChange={(e) => updateField(field.key, e.target.value)}
                  placeholder={`Enter ${field.label.toLowerCase()}`}
                  autoComplete="new-password"
                  name={`connector_${connector.name}_${field.key}`}
                  data-1p-ignore
                  data-lpignore="true"
                  data-form-type="other"
                />
              </FormField>
            ))}
            <div className="flex items-center gap-2 pt-2">
              <Button size="sm" onClick={handleSave} disabled={!allFieldsFilled || saving}>
                {saving ? 'Saving...' : 'Save Credentials'}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {/* Connected status */}
      {connector.connected && connector.auth_type !== 'none' && (
        <Card>
          <h4 className="text-sm font-semibold m-0 mb-3">Credentials</h4>
          <div className="text-xs text-text-muted bg-bg-subtle rounded-md px-3 py-2 font-[family-name:var(--font-mono)]">
            <div className="flex items-center gap-2">
              <Check size={12} className="text-success" />
              Credentials configured
            </div>
            {(connector.auth_fields || []).length > 0 && (
              <div className="mt-1.5 text-text-secondary">
                {connector.auth_fields.map((f) => (
                  <div key={f.key} className="text-[10px] opacity-70">
                    {f.label}: via admin panel or <span className="font-semibold">{f.env_var}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}

      {/* OAuth info (read-only for builtin connectors) */}
      {connector.auth_type === 'oauth2' && (connector.oauth_authorize_url || connector.oauth_token_url) && (
        <Card>
          <h4 className="text-sm font-semibold m-0 mb-3">OAuth 2.0 Configuration</h4>
          <div className="flex flex-col gap-3">
            {connector.oauth_authorize_url && (
              <div>
                <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Authorize URL</div>
                <div className="text-xs font-[family-name:var(--font-mono)] text-text-secondary break-all">{connector.oauth_authorize_url}</div>
              </div>
            )}
            {connector.oauth_token_url && (
              <div>
                <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Token URL</div>
                <div className="text-xs font-[family-name:var(--font-mono)] text-text-secondary break-all">{connector.oauth_token_url}</div>
              </div>
            )}
            {connector.oauth_scopes && connector.oauth_scopes.length > 0 && (
              <div>
                <div className="text-[10px] uppercase tracking-widest text-text-muted mb-1">Scopes</div>
                <div className="flex flex-wrap gap-1">
                  {connector.oauth_scopes.map((s) => (
                    <Badge key={s} variant="default" className="text-[10px]">{s}</Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* No auth */}
      {connector.auth_type === 'none' && (
        <Card>
          <div className="text-sm text-text-muted">
            This connector does not require authentication.
          </div>
        </Card>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={handleTest}
          disabled={testing}
        >
          {testing ? 'Testing...' : 'Test Connection'}
        </Button>
        {connector.connected && (
          <Button size="sm" variant="danger" onClick={handleDisconnect}>
            Disconnect
          </Button>
        )}
      </div>
    </div>
  );

  /* ── Tab: Tools ── */
  const toolsTab = (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h4 className="text-sm font-semibold m-0">Available MCP Tools</h4>
          {tools && (
            <p className="text-xs text-text-muted m-0 mt-1">{tools.length} tool{tools.length !== 1 ? 's' : ''} available</p>
          )}
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={handleDiscoverTools}
          disabled={discovering}
        >
          <Wrench size={14} className="mr-1" />
          {discovering ? 'Discovering...' : tools ? 'Refresh' : 'Discover Tools'}
        </Button>
      </div>

      {!tools && !discovering && (
        <Card>
          <div className="text-center py-6 text-text-muted">
            <Wrench size={24} className="mx-auto mb-2 opacity-40" />
            <p className="text-sm">Click &ldquo;Discover Tools&rdquo; to connect to the MCP server and list available tools.</p>
            {!connector.connected && connector.auth_type !== 'none' && (
              <p className="text-xs mt-1 text-amber-500">Configure credentials first in the Configuration tab.</p>
            )}
          </div>
        </Card>
      )}

      {discovering && (
        <div className="flex flex-col gap-2">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      )}

      {tools && tools.length === 0 && (
        <Card>
          <div className="text-center py-6 text-text-muted">
            <p className="text-sm">No tools discovered. The MCP server may not expose any tools.</p>
          </div>
        </Card>
      )}

      {tools && tools.length > 0 && (
        <div className="flex flex-col gap-2">
          {tools.map((tool) => (
            <div key={tool.name} className="border border-border rounded-lg overflow-hidden bg-bg-surface">
              <button
                type="button"
                onClick={() => setExpandedTool(expandedTool === tool.name ? null : tool.name)}
                className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-transparent border-0 cursor-pointer text-left hover:bg-bg-subtle/50 transition-colors"
              >
                <div className="min-w-0">
                  <div className="font-semibold text-sm text-text-primary font-[family-name:var(--font-mono)]">{tool.name}</div>
                  {tool.description && (
                    <p className="text-xs text-text-muted m-0 mt-0.5">{tool.description}</p>
                  )}
                </div>
                <div className="shrink-0 text-text-muted">
                  {expandedTool === tool.name
                    ? <ChevronDown size={14} />
                    : <ChevronRight size={14} />
                  }
                </div>
              </button>
              {expandedTool === tool.name && tool.input_schema && (
                <div className="border-t border-border px-4 py-3">
                  <div className="text-[10px] uppercase tracking-widest text-text-muted mb-2">Input Schema</div>
                  <pre className="text-xs font-[family-name:var(--font-mono)] text-text-secondary bg-bg-subtle rounded-md p-3 overflow-x-auto m-0">
                    {JSON.stringify(tool.input_schema, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );

  /* ── Page ── */
  return (
    <div className="max-w-6xl mx-auto">
      {/* Custom header with back link */}
      <div className="flex items-start justify-between mb-lg">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <Link
              href="/settings/services"
              className="text-text-muted hover:text-text-primary transition-colors"
            >
              <ArrowLeft size={18} />
            </Link>
            <Plug size={20} className="text-text-muted" />
            <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">
              {connector.display_name}
            </h1>
          </div>
          {connector.description && (
            <p className="mt-0 text-sm text-text-secondary">{connector.description}</p>
          )}
        </div>
      </div>
      <Tabs
        tabs={[
          { id: 'overview', label: 'Overview' },
          { id: 'configuration', label: 'Configuration' },
          { id: 'tools', label: `Tools${healthResult?.tool_count ? ` (${healthResult.tool_count})` : ''}` },
        ]}
        active={activeTab}
        onChange={setActiveTab}
      />

      <div className="mt-lg">
        {activeTab === 'overview' && overviewTab}
        {activeTab === 'configuration' && configTab}
        {activeTab === 'tools' && toolsTab}
      </div>
    </div>
  );
}
