'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { TokenInfo, CreateTokenResponse } from '@/utils/types';
import {
  Card, Button, Badge, Skeleton, EmptyState, ConfirmDialog, useToast,
  FormField, TextInput, Select,
} from '@/components/ui/legacy';
import { ResponsiveTable } from '@/components/responsive-table';
import { HelpPanel } from '@/components/help-panel';

const SCOPE_OPTIONS = [
  { value: 'read-only', label: 'Read only — list agents, view runs' },
  { value: 'read-write', label: 'Read/write — chat, run agents, manage sessions' },
  { value: 'admin', label: 'Admin — full access including settings' },
];

const EXPIRY_OPTIONS = [
  { value: '0', label: 'Never expires' },
  { value: '3600', label: '1 hour' },
  { value: '86400', label: '24 hours' },
  { value: '604800', label: '7 days' },
  { value: '2592000', label: '30 days' },
  { value: '7776000', label: '90 days' },
  { value: '31536000', label: '1 year' },
];

export default function TokensPage() {
  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [scope, setScope] = useState('read-write');
  const [expiresIn, setExpiresIn] = useState('2592000');
  const [creating, setCreating] = useState(false);
  const [newToken, setNewToken] = useState<CreateTokenResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const [deleteId, setDeleteId] = useState<string | null>(null);

  const fetchTokens = useCallback(async () => {
    try {
      const data = await adminApi.listTokens();
      setTokens(data);
      setError(null);
    } catch {
      setError('Failed to load tokens.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTokens(); }, [fetchTokens]);

  async function handleCreate() {
    if (!name) return;
    setCreating(true);
    setNewToken(null);
    try {
      const data = await adminApi.createToken(name, [scope], Number(expiresIn));
      setNewToken(data);
      setShowForm(false);
      setName('');
      toast('success', 'Token created — copy it now');
      await fetchTokens();
    } catch {
      setError('Failed to create token.');
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(tokenId: string) {
    try {
      await adminApi.revokeToken(tokenId);
      toast('success', 'Token revoked');
      await fetchTokens();
    } catch {
      setError('Failed to revoke token.');
    }
  }

  async function handleDelete() {
    if (!deleteId) return;
    try {
      await adminApi.deleteToken(deleteId);
      toast('success', 'Token deleted');
      await fetchTokens();
    } catch {
      setError('Failed to delete token.');
    } finally {
      setDeleteId(null);
    }
  }

  function handleCopy(text: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  function formatExpiry(ts: number): string {
    if (!ts) return 'Never';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function maskToken(t: { token_id: string; token_suffix?: string }): string {
    const suffix = t.token_suffix ?? t.token_id.slice(-4);
    return `****${suffix}`;
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex justify-between items-center mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">API Tokens</h1>
          <p className="m-0 text-sm text-text-secondary">
            Tokens authenticate SDK, CLI, and API access. Each token is shown once at creation.
          </p>
        </div>
        <Button onClick={() => { setShowForm(!showForm); setNewToken(null); }}>
          {showForm ? 'Cancel' : '+ Create Token'}
        </Button>
      </div>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-4 py-3 text-error text-sm mb-md" role="alert">
          {error}
        </div>
      )}

      {/* Show-once token banner */}
      {newToken && (
        <div className="bg-success-light border border-success/20 rounded-xl px-5 py-4 mb-lg">
          <p className="text-sm font-semibold text-success mb-2">
            Token created — copy it now. It will not be shown again.
          </p>
          <div className="flex items-center gap-3 mb-3">
            <code className="flex-1 px-3 py-2 bg-bg-surface rounded-lg text-[13px] font-[family-name:var(--font-mono)] break-all border border-border">
              {newToken.token}
            </code>
            <Button onClick={() => handleCopy(newToken.token)} size="sm">
              {copied ? 'Copied!' : 'Copy'}
            </Button>
          </div>
          {/* CLI snippet */}
          <p className="text-xs text-text-muted mb-1">CLI setup:</p>
          <pre className="bg-bg-deep text-primary text-xs rounded-lg px-4 py-3 overflow-x-auto font-[family-name:var(--font-mono)]">
            {`export SAGEWAI_API_KEY=${newToken.token}\nsagewai config set api-key $SAGEWAI_API_KEY`}
          </pre>
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <Card className="mb-lg">
          <h3 className="mt-0 mb-md text-base font-semibold">New Token</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-md mb-md">
            <FormField label="Token name" required>
              <TextInput
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. ci-deploy, scout-agent"
              />
            </FormField>
            <FormField label="Scope" hint="Determines what this token can do">
              <Select value={scope} onChange={(e) => setScope(e.target.value)}>
                {SCOPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </Select>
            </FormField>
            <FormField label="Expiry">
              <Select value={expiresIn} onChange={(e) => setExpiresIn(e.target.value)}>
                {EXPIRY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </Select>
            </FormField>
          </div>
          <Button onClick={handleCreate} disabled={creating || !name}>
            {creating ? 'Creating…' : 'Create Token'}
          </Button>
        </Card>
      )}

      {/* Token list */}
      <Card>
        {loading ? (
          <Skeleton lines={4} />
        ) : tokens.length === 0 ? (
          <EmptyState
            title="No API tokens"
            description="Create a token to authenticate SDK, CLI, or direct API calls."
            actionLabel="Create your first token"
            onAction={() => setShowForm(true)}
          />
        ) : (
          <ResponsiveTable
            columns={[
              { key: 'token', label: 'Token' },
              { key: 'name', label: 'Name' },
              { key: 'scope', label: 'Scope' },
              { key: 'status', label: 'Status' },
              { key: 'expires', label: 'Expires' },
              { key: 'actions', label: 'Actions' },
            ]}
            rows={tokens.map((t) => ({
              token: <span className="font-[family-name:var(--font-mono)] text-[12px] text-text-muted">{maskToken(t)}</span>,
              name: <span className="font-medium">{t.agent_name}</span>,
              scope: <Badge variant="default">{t.scopes.join(', ')}</Badge>,
              status: <Badge variant={t.status === 'active' ? 'success' : 'error'}>{t.status}</Badge>,
              expires: <span className="text-[13px] text-text-muted">{formatExpiry(t.expires_at)}</span>,
              actions: (
                <div className="flex gap-2">
                  {t.status === 'active' && (
                    <Button size="sm" variant="secondary" onClick={() => handleRevoke(t.token_id)}>
                      Revoke
                    </Button>
                  )}
                  <Button size="sm" variant="secondary" className="text-error" onClick={() => setDeleteId(t.token_id)}>
                    Delete
                  </Button>
                </div>
              ),
            }))}
          />
        )}
      </Card>

      {/* CLI reference */}
      <div className="mt-lg p-md bg-bg-subtle rounded-xl border border-border">
        <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-2">CLI setup reference</p>
        <pre className="text-xs font-[family-name:var(--font-mono)] text-text-secondary overflow-x-auto whitespace-pre-wrap">
          {`# Set in environment\nexport SAGEWAI_API_KEY=swk_...\n\n# Or persist in CLI config\nsagewai config set api-key $SAGEWAI_API_KEY`}
        </pre>
      </div>

      <ConfirmDialog
        open={!!deleteId}
        onClose={() => setDeleteId(null)}
        onConfirm={handleDelete}
        title="Delete Token"
        message="Permanently delete this token? Any integrations using it will stop working immediately."
      />

      <HelpPanel title="API Tokens">
        <h3>Scopes</h3>
        <ul>
          <li><strong>read-only</strong> — list agents, view runs and sessions</li>
          <li><strong>read-write</strong> — chat with agents, trigger runs, manage sessions</li>
          <li><strong>admin</strong> — full access including settings, tokens, and configuration</li>
        </ul>
        <h3>Expiry</h3>
        <p>Tokens can be set to expire after a fixed duration. Expired tokens are automatically rejected. Use short-lived tokens for CI/CD and long-lived tokens for development.</p>
        <h3>CLI Usage</h3>
        <p>Set the token in your environment:</p>
        <p><code>export SAGEWAI_API_KEY=sat-...</code></p>
        <p>Or persist it:</p>
        <p><code>sagewai config set api-key $SAGEWAI_API_KEY</code></p>
        <h3>Security</h3>
        <p>Tokens are shown only once at creation. Only a SHA-256 hash is stored. Revoked tokens are immediately rejected.</p>
      </HelpPanel>
    </div>
  );
}
