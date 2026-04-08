'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { LLMProvider, Workspace } from '@/utils/types';
import { Card, Button, Badge, EmptyState, Skeleton, useToast } from '@sagecurator/ui';

const PROVIDER_OPTIONS = ['openai', 'anthropic', 'google', 'mistral', 'cohere', 'groq'];

export default function ProvidersPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [wsId, setWsId] = useState('');
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState<number | null>(null);
  const { toast } = useToast();

  // Add form
  const [showForm, setShowForm] = useState(false);
  const [providerName, setProviderName] = useState('openai');
  const [apiKey, setApiKey] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [isDefault, setIsDefault] = useState(false);
  const [adding, setAdding] = useState(false);

  const fetchWorkspaces = useCallback(async () => {
    try {
      const data = await adminApi.listWorkspaces();
      setWorkspaces(data);
      if (data.length > 0 && !wsId) setWsId(data[0].id);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [wsId]);

  const fetchProviders = useCallback(async () => {
    if (!wsId) return;
    try {
      const data = await adminApi.listProviders(wsId);
      setProviders(data);
    } catch { /* ignore */ }
  }, [wsId]);

  useEffect(() => { fetchWorkspaces(); }, [fetchWorkspaces]);
  useEffect(() => { fetchProviders(); }, [fetchProviders]);

  async function handleAdd() {
    if (!apiKey || !wsId) return;
    setAdding(true);
    try {
      await adminApi.addProvider(wsId, providerName, apiKey, displayName, isDefault);
      setShowForm(false);
      setApiKey('');
      setDisplayName('');
      setIsDefault(false);
      toast('success', 'Provider added');
      fetchProviders();
    } catch {
      toast('error', 'Failed to add provider');
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(providerId: number) {
    if (!wsId || !confirm('Remove this provider?')) return;
    try {
      await adminApi.deleteProvider(wsId, providerId);
      toast('success', 'Provider removed');
      fetchProviders();
    } catch { /* ignore */ }
  }

  async function handleTest(providerId: number) {
    if (!wsId) return;
    setTesting(providerId);
    try {
      const result = await adminApi.testProvider(wsId, providerId);
      toast(result.status === 'ok' ? 'success' : 'error', `${result.provider}: ${result.status} — ${result.detail}`);
    } catch {
      toast('error', 'Connection test failed.');
    } finally {
      setTesting(null);
    }
  }

  if (loading) return <Skeleton lines={5} />;

  return (
    <div className="max-w-[800px] mx-auto">
      <div className="flex justify-between items-center mb-lg">
        <h1 className="m-0 text-2xl font-bold font-[family-name:var(--font-heading)]">LLM Providers</h1>
        <Button onClick={() => setShowForm(!showForm)}>
          {showForm ? 'Cancel' : 'Add Provider'}
        </Button>
      </div>

      {/* Add provider form */}
      {showForm && (
        <Card className="mb-lg">
          <h3 className="mt-0 mb-md text-[15px] font-semibold font-[family-name:var(--font-heading)]">Add LLM Provider</h3>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <div>
              <span className="text-xs font-medium text-text-secondary block mb-1">Provider</span>
              <select value={providerName} onChange={(e) => setProviderName(e.target.value)} className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface box-border">
                {PROVIDER_OPTIONS.map((p) => (
                  <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
                ))}
              </select>
            </div>
            <div>
              <span className="text-xs font-medium text-text-secondary block mb-1">Display Name</span>
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)}
                placeholder="e.g., Production OpenAI" className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface box-border" />
            </div>
          </div>
          <div className="mb-3">
            <span className="text-xs font-medium text-text-secondary block mb-1">API Key</span>
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..." className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface box-border" />
          </div>
          <div className="flex items-center gap-2 mb-4">
            <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} id="isDefault" />
            <label htmlFor="isDefault" className="text-[13px] text-text-secondary">Set as default provider</label>
          </div>
          <Button onClick={handleAdd} disabled={adding || !apiKey}>
            {adding ? 'Adding...' : 'Add Provider'}
          </Button>
        </Card>
      )}

      {/* Provider cards */}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-md">
        {providers.length === 0 ? (
          <Card>
            <EmptyState title="No Providers" description="No providers configured. Add one to get started." />
          </Card>
        ) : (
          providers.map((p) => (
            <Card key={p.id} className={p.is_default ? 'border-2 border-primary' : ''}>
              <div className="flex justify-between items-center mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold">
                    {p.provider_name.charAt(0).toUpperCase() + p.provider_name.slice(1)}
                  </span>
                  {p.is_default && (
                    <Badge variant="info">Default</Badge>
                  )}
                </div>
              </div>
              {p.display_name && <div className="text-[13px] text-text-muted mb-2">{p.display_name}</div>}
              <div className="text-[13px] text-text-muted font-[family-name:var(--font-mono)] mb-4">{p.api_key_masked}</div>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => handleTest(p.id)} disabled={testing === p.id}>
                  {testing === p.id ? 'Testing...' : 'Test'}
                </Button>
                <Button variant="secondary" className="text-error border-error" onClick={() => handleDelete(p.id)}>
                  Delete
                </Button>
              </div>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}
