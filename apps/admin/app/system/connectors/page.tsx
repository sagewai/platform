'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import Link from 'next/link';
import { PageLayout, Card, Button, Badge, FormField, TextInput, useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import { ConnectorHealthBadge } from '@/components/connector-health-badge';
import type { ConnectorCatalogItem, ConnectorHealthResult, ConnectorAuthField, CustomConnectorRequest } from '@/utils/types';
import {
  ChevronDown,
  ChevronRight,
  Check,
  X,
  ExternalLink,
  Plug,
  Webhook,
  Radio,
  RefreshCw,
  MessageSquare,
  CreditCard,
  Database,
  Mail,
  Calendar,
  FileText,
  ShoppingCart,
  Folder,
  Search,
  Users,
  Building2,
  Calculator,
  Megaphone,
  TrendingUp,
  Blocks,
  Sparkles,
  Copy,
  Plus,
  Trash2,
  Wrench,
  Filter,
} from 'lucide-react';

/* ─── Category icon mapping ─── */

const CATEGORY_ICONS: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  search: Search,
  communication: MessageSquare,
  productivity: Calendar,
  commerce: CreditCard,
  crm: Users,
  erp: Building2,
  accounting: Calculator,
  marketing: Megaphone,
  finance: TrendingUp,
  aggregator: Blocks,
  data: Database,
  email: Mail,
  documents: FileText,
  shopping: ShoppingCart,
  custom: Wrench,
};

function getCategoryIcon(category: string) {
  const key = category.toLowerCase().replace(/[^a-z]/g, '');
  for (const [k, icon] of Object.entries(CATEGORY_ICONS)) {
    if (key.includes(k)) return icon;
  }
  return Folder;
}

/* ─── Components ─── */

function AuthTypeBadge({ authType }: { authType: string }) {
  const labels: Record<string, string> = {
    env_key: 'ENV Key',
    api_key: 'API Key',
    oauth2: 'OAuth 2.0',
    none: 'No Auth',
  };
  return (
    <Badge variant="info" className="text-[10px]">
      {labels[authType] || authType}
    </Badge>
  );
}

function CapabilityBadges({ connector }: { connector: ConnectorCatalogItem }) {
  return (
    <div className="flex items-center gap-1">
      {connector.supports_webhook && (
        <span title="Supports webhooks" className="text-text-muted">
          <Webhook size={12} />
        </span>
      )}
      {connector.supports_listener && (
        <span title="Supports listeners" className="text-text-muted">
          <Radio size={12} />
        </span>
      )}
      {connector.supports_poller && (
        <span title="Supports polling" className="text-text-muted">
          <RefreshCw size={12} />
        </span>
      )}
    </div>
  );
}

interface ConnectorCardProps {
  connector: ConnectorCatalogItem;
  onSave: (name: string, credentials: Record<string, string>) => Promise<void>;
  onTest: (name: string) => Promise<void>;
  onDelete: (name: string) => Promise<void>;
  onDeleteCustom?: (name: string) => Promise<void>;
  testingName: string | null;
  testResults: Record<string, ConnectorHealthResult>;
}

function ConnectorCard({ connector, onSave, onTest, onDelete, onDeleteCustom, testingName, testResults }: ConnectorCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const f of connector.auth_fields || []) init[f.key] = '';
    return init;
  });
  const result = testResults[connector.name];

  const updateField = (key: string, value: string) => {
    setFieldValues((prev) => ({ ...prev, [key]: value }));
  };

  const allFieldsFilled = (connector.auth_fields || []).every((f) => fieldValues[f.key]?.trim());

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-bg-subtle/50 transition-colors">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-3 min-w-0 bg-transparent border-0 cursor-pointer text-left flex-1"
        >
          {expanded
            ? <ChevronDown size={14} className="text-text-muted shrink-0" />
            : <ChevronRight size={14} className="text-text-muted shrink-0" />
          }
          <Plug size={16} className="text-text-muted shrink-0" />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Link
                href={`/settings/services/${connector.name}`}
                className="font-semibold text-sm text-text-primary hover:text-primary transition-colors no-underline"
                onClick={(e) => e.stopPropagation()}
              >
                {connector.display_name}
              </Link>
              <AuthTypeBadge authType={connector.auth_type} />
              {connector.is_custom && <Badge variant="warning" className="text-[10px]">Custom</Badge>}
              <CapabilityBadges connector={connector} />
            </div>
            <p className="text-xs text-text-muted m-0 mt-0.5 truncate">{connector.description}</p>
          </div>
        </button>
        <div className="shrink-0">
          <ConnectorHealthBadge connected={connector.connected} health={result} />
        </div>
      </div>

      {/* Body */}
      {expanded && (
        <div className="border-t border-border px-4 py-3 flex flex-col gap-3">
          {/* Agent description — when to use this connector */}
          {connector.agent_description && (
            <div className="text-xs text-text-secondary bg-bg-subtle rounded-md px-3 py-2">
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
                  onClick={() => navigator.clipboard.writeText(connector.example_prompt)}
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

          {/* Credential fields — rendered dynamically from auth_fields */}
          {connector.auth_type !== 'none' && !connector.connected && (connector.auth_fields || []).length > 0 && (
            <form autoComplete="off" onSubmit={(e) => e.preventDefault()} className="flex flex-col gap-2">
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
                    type="text"
                    value={fieldValues[field.key] || ''}
                    onChange={(e) => updateField(field.key, e.target.value)}
                    placeholder={`Enter ${field.label.toLowerCase()}`}
                    autoComplete="one-time-code"
                    name={`connector_${connector.name}_${field.key}`}
                    data-1p-ignore
                    data-lpignore="true"
                    data-form-type="other"
                    className={field.secret ? 'password-mask' : ''}
                  />
                </FormField>
              ))}
            </form>
          )}

          {/* Connected status message */}
          {connector.connected && connector.auth_type !== 'none' && (
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
          )}

          {/* Env var hint for no-auth connectors */}
          {connector.auth_type === 'none' && (
            <div className="text-xs text-text-muted">
              This connector does not require authentication.
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2">
            {connector.auth_type !== 'none' && !connector.connected && (
              <Button
                size="sm"
                onClick={() => onSave(connector.name, fieldValues)}
                disabled={!allFieldsFilled}
              >
                Save Credentials
              </Button>
            )}
            {(connector.connected || connector.auth_type === 'none') && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => onTest(connector.name)}
                disabled={testingName === connector.name}
              >
                {testingName === connector.name ? 'Testing...' : 'Test Connection'}
              </Button>
            )}
            {connector.connected && (
              <Button
                size="sm"
                variant="danger"
                onClick={() => onDelete(connector.name)}
              >
                Disconnect
              </Button>
            )}
            {connector.is_custom && onDeleteCustom && (
              <Button
                size="sm"
                variant="danger"
                onClick={() => onDeleteCustom(connector.name)}
              >
                Remove
              </Button>
            )}
            {connector.docs_url && (
              <a
                href={connector.docs_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-text-muted hover:text-primary flex items-center gap-1 ml-auto transition-colors"
              >
                Docs <ExternalLink size={11} />
              </a>
            )}
          </div>

          {/* Test result */}
          {result && (
            <div className={`rounded-md px-3 py-2 text-xs ${result.status === 'healthy' ? 'bg-success/10 text-success' : 'bg-error/10 text-error'}`}>
              <div className="flex items-center gap-1.5 font-medium">
                {result.status === 'healthy' ? <Check size={12} /> : <X size={12} />}
                {result.status === 'healthy'
                  ? `Healthy${result.latency_ms ? ` (${result.latency_ms}ms)` : ''}`
                  : result.error || `Status: ${result.status}`}
              </div>
              {result.status === 'healthy' && result.tool_count != null && result.tool_count > 0 && (
                <div className="mt-1.5 text-text-secondary">
                  <span className="font-medium">{result.tool_count} tools available</span> for your AI agents
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Custom Connector Registration Form ─── */

const AUTH_TYPE_OPTIONS = [
  { value: 'api_key', label: 'API Key' },
  { value: 'oauth2', label: 'OAuth 2.0' },
  { value: 'env_key', label: 'ENV Key' },
  { value: 'none', label: 'No Auth' },
];

const CATEGORY_OPTIONS = [
  'custom', 'accounting', 'aggregator', 'communication', 'commerce', 'crm',
  'data', 'documents', 'email', 'erp', 'finance', 'marketing', 'productivity', 'search',
];

function CustomConnectorForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (data: CustomConnectorRequest) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('custom');
  const [mcpCommand, setMcpCommand] = useState('');
  const [authType, setAuthType] = useState('api_key');
  const [authFields, setAuthFields] = useState<ConnectorAuthField[]>([]);
  const [docsUrl, setDocsUrl] = useState('');
  const [agentDescription, setAgentDescription] = useState('');
  const [examplePrompt, setExamplePrompt] = useState('');
  const [oauthAuthorizeUrl, setOauthAuthorizeUrl] = useState('');
  const [oauthTokenUrl, setOauthTokenUrl] = useState('');
  const [oauthScopes, setOauthScopes] = useState('');
  const [supportsWebhook, setSupportsWebhook] = useState(false);
  const [supportsListener, setSupportsListener] = useState(false);
  const [supportsPoller, setSupportsPoller] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const addAuthField = () => {
    setAuthFields((prev) => [
      ...prev,
      { key: '', label: '', env_var: '', secret: true, hint: '' },
    ]);
  };

  const removeAuthField = (idx: number) => {
    setAuthFields((prev) => prev.filter((_, i) => i !== idx));
  };

  const updateAuthField = (idx: number, field: Partial<ConnectorAuthField>) => {
    setAuthFields((prev) => prev.map((f, i) => (i === idx ? { ...f, ...field } : f)));
  };

  const canSubmit = name.trim() && displayName.trim() && mcpCommand.trim();

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSubmit({
        name: name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_'),
        display_name: displayName.trim(),
        description: description.trim(),
        category,
        mcp_command: mcpCommand.trim().split(/\s+/),
        auth_type: authType,
        auth_fields: authFields.filter((f) => f.key && f.label),
        docs_url: docsUrl.trim() || undefined,
        agent_description: agentDescription.trim() || undefined,
        example_prompt: examplePrompt.trim() || undefined,
        oauth_authorize_url: oauthAuthorizeUrl.trim() || undefined,
        oauth_token_url: oauthTokenUrl.trim() || undefined,
        oauth_scopes: oauthScopes.trim() ? oauthScopes.split(',').map((s) => s.trim()) : undefined,
        supports_webhook: supportsWebhook,
        supports_listener: supportsListener,
        supports_poller: supportsPoller,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-2 mb-1">
          <Wrench size={16} className="text-primary" />
          <h3 className="text-base font-semibold m-0">Register Custom Connector</h3>
        </div>

        {/* Basic info */}
        <div className="grid grid-cols-2 gap-3">
          <FormField label="Name (slug)">
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my_connector"
            />
          </FormField>
          <FormField label="Display Name">
            <TextInput
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="My Connector"
            />
          </FormField>
        </div>

        <FormField label="Description">
          <TextInput
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Brief description of what this connector does"
          />
        </FormField>

        <div className="grid grid-cols-2 gap-3">
          <FormField label="Category">
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary"
            >
              {CATEGORY_OPTIONS.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </FormField>
          <FormField label="Auth Type">
            <select
              value={authType}
              onChange={(e) => setAuthType(e.target.value)}
              className="w-full h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary"
            >
              {AUTH_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </FormField>
        </div>

        <FormField label="MCP Command" hint="Space-separated command to start the MCP server (e.g. python -m my_server)">
          <TextInput
            value={mcpCommand}
            onChange={(e) => setMcpCommand(e.target.value)}
            placeholder="python -m my_server"
          />
        </FormField>

        {/* Auth fields */}
        {authType !== 'none' && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-text-primary">Auth Fields</span>
              <Button size="sm" variant="secondary" onClick={addAuthField}>
                <Plus size={12} className="mr-1" /> Add Field
              </Button>
            </div>
            {authFields.map((field, idx) => (
              <div key={idx} className="flex gap-2 mb-2 items-start">
                <TextInput
                  value={field.key}
                  onChange={(e) => updateAuthField(idx, { key: e.target.value })}
                  placeholder="key"
                  className="flex-1"
                />
                <TextInput
                  value={field.label}
                  onChange={(e) => updateAuthField(idx, { label: e.target.value })}
                  placeholder="Label"
                  className="flex-1"
                />
                <TextInput
                  value={field.env_var}
                  onChange={(e) => updateAuthField(idx, { env_var: e.target.value })}
                  placeholder="ENV_VAR"
                  className="flex-1"
                />
                <label className="flex items-center gap-1 text-xs text-text-muted shrink-0 pt-2">
                  <input
                    type="checkbox"
                    checked={field.secret}
                    onChange={(e) => updateAuthField(idx, { secret: e.target.checked })}
                  />
                  Secret
                </label>
                <button
                  type="button"
                  onClick={() => removeAuthField(idx)}
                  className="text-error bg-transparent border-none cursor-pointer p-1 shrink-0"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
            {authFields.length === 0 && (
              <p className="text-xs text-text-muted m-0">No auth fields configured. Click &quot;Add Field&quot; to add credential fields.</p>
            )}
          </div>
        )}

        {/* OAuth2 fields */}
        {authType === 'oauth2' && (
          <div className="grid grid-cols-2 gap-3">
            <FormField label="OAuth Authorize URL">
              <TextInput
                value={oauthAuthorizeUrl}
                onChange={(e) => setOauthAuthorizeUrl(e.target.value)}
                placeholder="https://example.com/oauth/authorize"
              />
            </FormField>
            <FormField label="OAuth Token URL">
              <TextInput
                value={oauthTokenUrl}
                onChange={(e) => setOauthTokenUrl(e.target.value)}
                placeholder="https://example.com/oauth/token"
              />
            </FormField>
            <FormField label="OAuth Scopes" hint="Comma-separated">
              <TextInput
                value={oauthScopes}
                onChange={(e) => setOauthScopes(e.target.value)}
                placeholder="read, write"
              />
            </FormField>
          </div>
        )}

        {/* Agent metadata */}
        <FormField label="Agent Description" hint="When/why an AI agent should use this connector">
          <TextInput
            value={agentDescription}
            onChange={(e) => setAgentDescription(e.target.value)}
            placeholder="Use when the agent needs to..."
          />
        </FormField>

        <FormField label="Example Prompt" hint="Example user prompt demonstrating usage">
          <TextInput
            value={examplePrompt}
            onChange={(e) => setExamplePrompt(e.target.value)}
            placeholder="Find all open issues assigned to me..."
          />
        </FormField>

        <FormField label="Docs URL">
          <TextInput
            value={docsUrl}
            onChange={(e) => setDocsUrl(e.target.value)}
            placeholder="https://docs.example.com"
          />
        </FormField>

        {/* Event support */}
        <div>
          <span className="text-sm font-medium text-text-primary mb-2 block">Event Support</span>
          <div className="flex gap-4">
            <label className="flex items-center gap-1.5 text-sm text-text-secondary">
              <input type="checkbox" checked={supportsWebhook} onChange={(e) => setSupportsWebhook(e.target.checked)} />
              Webhooks
            </label>
            <label className="flex items-center gap-1.5 text-sm text-text-secondary">
              <input type="checkbox" checked={supportsListener} onChange={(e) => setSupportsListener(e.target.checked)} />
              Listeners
            </label>
            <label className="flex items-center gap-1.5 text-sm text-text-secondary">
              <input type="checkbox" checked={supportsPoller} onChange={(e) => setSupportsPoller(e.target.checked)} />
              Polling
            </label>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 pt-2 border-t border-border">
          <Button size="sm" onClick={handleSubmit} disabled={!canSubmit || submitting}>
            {submitting ? 'Registering...' : 'Register Connector'}
          </Button>
          <Button size="sm" variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </Card>
  );
}

/* ─── Main Page ─── */

const BATCH_SIZE = 4;

export default function ConnectorsSettingsPage() {
  const [connectors, setConnectors] = useState<ConnectorCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [testingName, setTestingName] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ConnectorHealthResult>>({});
  const [healthPolling, setHealthPolling] = useState(false);
  const [showCustomForm, setShowCustomForm] = useState(false);
  const { toast } = useToast();

  /* ── Search & Filter state ── */
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'connected' | 'disconnected'>('all');
  const [categoryFilter, setCategoryFilter] = useState('all');

  const hasFilters = search.trim() !== '' || statusFilter !== 'all' || categoryFilter !== 'all';

  /* ── Unique categories for filter dropdown ── */
  const categories = useMemo(() => {
    const cats = new Set(connectors.map((c) => c.category || 'Other'));
    return ['all', ...Array.from(cats).sort()];
  }, [connectors]);

  /* ── Filtered flat list (when filters active) ── */
  const filtered = useMemo(() => {
    if (!hasFilters) return connectors;
    const q = search.toLowerCase();
    return connectors.filter((c) => {
      if (statusFilter === 'connected' && !c.connected) return false;
      if (statusFilter === 'disconnected' && c.connected) return false;
      if (categoryFilter !== 'all' && (c.category || 'Other') !== categoryFilter) return false;
      if (q) {
        const haystack = `${c.name} ${c.display_name} ${c.description} ${c.category}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [connectors, search, statusFilter, categoryFilter, hasFilters]);

  /* ── Category-grouped view (when no filters) ── */
  const categorized = useMemo(() => {
    const groups: Record<string, ConnectorCatalogItem[]> = {};
    for (const c of connectors) {
      const cat = c.category || 'Other';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(c);
    }
    return Object.entries(groups).sort(([a], [b]) => {
      if (a === 'custom') return -1;
      if (b === 'custom') return 1;
      if (a === 'Other') return 1;
      if (b === 'Other') return -1;
      return a.localeCompare(b);
    });
  }, [connectors]);

  /* ── Data loading ── */
  const loadConnectors = useCallback(async () => {
    try {
      const data = await adminApi.listConnectors();
      setConnectors(data);
      return data;
    } catch {
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  /* ── Health polling — auto-test connected connectors ── */
  const pollHealth = useCallback(async (items: ConnectorCatalogItem[]) => {
    const connected = items.filter((c) => c.connected || c.auth_type === 'none');
    if (connected.length === 0) return;
    setHealthPolling(true);
    for (let i = 0; i < connected.length; i += BATCH_SIZE) {
      const batch = connected.slice(i, i + BATCH_SIZE);
      const results = await Promise.allSettled(
        batch.map((c) => adminApi.testConnector(c.name)),
      );
      const updates: Record<string, ConnectorHealthResult> = {};
      results.forEach((r, idx) => {
        if (r.status === 'fulfilled') {
          updates[batch[idx].name] = r.value;
        }
      });
      setTestResults((prev) => ({ ...prev, ...updates }));
    }
    setHealthPolling(false);
  }, []);

  useEffect(() => {
    loadConnectors().then((data) => {
      if (data.length > 0) pollHealth(data);
    });
  }, [loadConnectors, pollHealth]);

  const handleRefreshHealth = async () => {
    const data = await loadConnectors();
    if (data.length > 0) await pollHealth(data);
  };

  /* ── Handlers ── */
  const handleSave = async (name: string, credentials: Record<string, string>) => {
    const connector = connectors.find((c) => c.name === name);
    try {
      await adminApi.saveConnectorCredentials(name, credentials);
      toast('success', `${connector?.display_name || name} configured`);
      await loadConnectors();
    } catch {
      toast('error', 'Failed to save connector credentials');
    }
  };

  const handleTest = async (name: string) => {
    setTestingName(name);
    try {
      const result = await adminApi.testConnector(name);
      setTestResults((prev) => ({ ...prev, [name]: result }));
      if (result.status === 'healthy') {
        toast('success', `Healthy${result.latency_ms ? ` (${result.latency_ms}ms)` : ''} — ${result.tool_count || 0} tools`);
      } else {
        toast('error', result.error || `Status: ${result.status}`);
      }
    } catch {
      toast('error', 'Test failed');
    } finally {
      setTestingName(null);
    }
  };

  const handleDelete = async (name: string) => {
    const connector = connectors.find((c) => c.name === name);
    try {
      await adminApi.deleteConnector(name);
      toast('success', `${connector?.display_name || name} disconnected`);
      setTestResults((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      await loadConnectors();
    } catch {
      toast('error', 'Failed to disconnect connector');
    }
  };

  const handleDeleteCustom = async (name: string) => {
    const connector = connectors.find((c) => c.name === name);
    try {
      await adminApi.deleteCustomConnector(name);
      toast('success', `${connector?.display_name || name} removed`);
      setTestResults((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      await loadConnectors();
    } catch {
      toast('error', 'Failed to remove custom connector');
    }
  };

  const handleRegisterCustom = async (data: CustomConnectorRequest) => {
    try {
      await adminApi.registerCustomConnector(data);
      toast('success', `${data.display_name} registered`);
      setShowCustomForm(false);
      await loadConnectors();
    } catch {
      toast('error', 'Failed to register custom connector');
    }
  };

  const connectedCount = connectors.filter((c) => c.connected).length;
  const healthyCount = Object.values(testResults).filter((r) => r.status === 'healthy').length;

  /* ── Render ── */
  return (
    <PageLayout
      title="Connectors"
      description={
        loading
          ? 'Loading connector catalog...'
          : `${connectedCount} of ${connectors.length} connected${healthyCount > 0 ? ` · ${healthyCount} healthy` : ''}`
      }
      actions={
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={handleRefreshHealth}
            disabled={healthPolling || loading}
          >
            <RefreshCw size={14} className={`mr-1 ${healthPolling ? 'animate-spin' : ''}`} />
            {healthPolling ? 'Checking...' : 'Refresh Health'}
          </Button>
          {!showCustomForm && (
            <Button size="sm" onClick={() => setShowCustomForm(true)}>
              <Plus size={14} className="mr-1" /> Register Custom
            </Button>
          )}
        </div>
      }
    >
      {/* Search & Filter Bar */}
      <div className="flex items-center gap-3 mb-lg">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search connectors..."
            className="w-full h-9 pl-9 pr-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary placeholder:text-text-muted focus:outline-none focus:border-primary transition-colors"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary bg-transparent border-none cursor-pointer p-0.5"
            >
              <X size={12} />
            </button>
          )}
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as 'all' | 'connected' | 'disconnected')}
          className="h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary"
        >
          <option value="all">All status</option>
          <option value="connected">Connected</option>
          <option value="disconnected">Disconnected</option>
        </select>
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary"
        >
          {categories.map((c) => (
            <option key={c} value={c}>{c === 'all' ? 'All categories' : c}</option>
          ))}
        </select>
        {hasFilters && (
          <button
            type="button"
            onClick={() => { setSearch(''); setStatusFilter('all'); setCategoryFilter('all'); }}
            className="text-xs text-text-muted hover:text-primary bg-transparent border-none cursor-pointer flex items-center gap-1"
          >
            <X size={12} /> Clear
          </button>
        )}
      </div>

      {/* Custom connector form */}
      {showCustomForm && (
        <div className="mb-xl">
          <CustomConnectorForm
            onSubmit={handleRegisterCustom}
            onCancel={() => setShowCustomForm(false)}
          />
        </div>
      )}

      {/* Filtered flat view */}
      {hasFilters ? (
        <>
          <div className="text-xs text-text-muted mb-sm">
            {filtered.length} connector{filtered.length !== 1 ? 's' : ''} matching filters
          </div>
          <div className="flex flex-col gap-2">
            {filtered.map((connector) => (
              <ConnectorCard
                key={connector.name}
                connector={connector}
                onSave={handleSave}
                onTest={handleTest}
                onDelete={handleDelete}
                onDeleteCustom={connector.is_custom ? handleDeleteCustom : undefined}
                testingName={testingName}
                testResults={testResults}
              />
            ))}
          </div>
          {filtered.length === 0 && (
            <Card>
              <div className="text-center py-6 text-text-muted">
                <Filter size={24} className="mx-auto mb-2 opacity-40" />
                <p className="text-sm">No connectors match your filters.</p>
              </div>
            </Card>
          )}
        </>
      ) : (
        /* Category-grouped view */
        <>
          {categorized.map(([category, items]) => {
            const CategoryIcon = getCategoryIcon(category);
            return (
              <section key={category} className="mb-xl">
                <div className="flex items-center gap-2 mb-sm">
                  <CategoryIcon size={16} className="text-primary" />
                  <h2 className="text-lg font-semibold m-0 font-[family-name:var(--font-heading)]">{category}</h2>
                </div>
                <div className="flex flex-col gap-2">
                  {items.map((connector) => (
                    <ConnectorCard
                      key={connector.name}
                      connector={connector}
                      onSave={handleSave}
                      onTest={handleTest}
                      onDelete={handleDelete}
                      onDeleteCustom={connector.is_custom ? handleDeleteCustom : undefined}
                      testingName={testingName}
                      testResults={testResults}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </>
      )}

      {!loading && connectors.length === 0 && (
        <Card>
          <div className="text-center py-8 text-text-muted">
            <Plug size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">No connectors available in the catalog.</p>
            <p className="text-xs mt-1">Register connector definitions in the SDK or add a custom connector above.</p>
          </div>
        </Card>
      )}
    </PageLayout>
  );
}
