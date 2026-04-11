'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { OrgSettings, TestLiteLLMResponse } from '@/utils/types';
import {
  Card,
  Button,
  FormField,
  TextInput,
  Skeleton,
  Badge,
  useToast,
} from '@/components/ui/legacy';
import { Check, X, Database, Network, Cpu, RefreshCw } from 'lucide-react';

export default function InfrastructureSettingsPage() {
  const [org, setOrg] = useState<OrgSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  // LiteLLM Proxy
  const [proxyUrl, setProxyUrl] = useState('');
  const [proxyApiKey, setProxyApiKey] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestLiteLLMResponse | null>(
    null,
  );

  // Milvus
  const [milvusUri, setMilvusUri] = useState('');

  // NebulaGraph
  const [nebulaHost, setNebulaHost] = useState('localhost');
  const [nebulaPort, setNebulaPort] = useState('9669');

  const loadOrg = useCallback(async () => {
    try {
      const data = await adminApi.getOrganization();
      setOrg(data);
      setProxyUrl(data.litellm_proxy_url || '');
      setMilvusUri(data.milvus_uri || '');
      setNebulaHost(data.nebula_host || 'localhost');
      setNebulaPort(String(data.nebula_port || 9669));
    } catch {
      setError('Failed to load infrastructure settings.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOrg();
  }, [loadOrg]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        org_name: org?.org_name || '',
        app_url: org?.app_url || '',
        contact_email: org?.contact_email || '',
        timezone: org?.timezone || 'UTC',
        industry: org?.industry || '',
        team_size: org?.team_size || '',
        litellm_proxy_url: proxyUrl,
        milvus_uri: milvusUri,
        nebula_host: nebulaHost,
        nebula_port: parseInt(nebulaPort, 10) || 9669,
      };
      // Only include API key if the user typed a new one
      if (proxyApiKey) {
        payload.litellm_api_key = proxyApiKey;
      }
      const updated = await adminApi.updateOrganization(payload);
      setOrg(updated);
      setProxyApiKey(''); // clear key field after save
      toast('success', 'Infrastructure settings saved');
    } catch {
      setError('Failed to save infrastructure settings.');
    } finally {
      setSaving(false);
    }
  }

  async function handleTestLiteLLM() {
    if (!proxyUrl.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await adminApi.testLiteLLM({
        proxy_url: proxyUrl,
        api_key: proxyApiKey || undefined,
      });
      setTestResult(result);
      if (result.healthy) {
        toast('success', `LiteLLM proxy healthy — ${result.models.length} models`);
      } else {
        toast('error', result.error || 'Connection failed');
      }
    } catch {
      setTestResult({ healthy: false, error: 'Request failed', models: [] });
      toast('error', 'Failed to test LiteLLM connection');
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-lg">
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Infrastructure
        </h1>
        <p className="m-0 text-sm text-text-secondary">
          Configure LLM proxy, vector database, and graph database connections.
        </p>
      </div>

      {error && (
        <div
          className="bg-error-light border border-error/20 rounded-lg px-4 py-3 text-error text-sm mb-md"
          role="alert"
        >
          {error}
        </div>
      )}

      {loading ? (
        <Card>
          <Skeleton lines={8} />
        </Card>
      ) : (
        <>
          {/* ─── LLM Proxy ─── */}
          <Card className="mb-md">
            <div className="flex items-center gap-2 mb-md">
              <Cpu size={16} className="text-primary" />
              <h2 className="text-lg font-semibold m-0 font-[family-name:var(--font-heading)]">
                LLM Proxy
              </h2>
              {org?.litellm_proxy_url && (
                <Badge variant="info">Configured</Badge>
              )}
            </div>
            <p className="text-sm text-text-muted mt-0 mb-md">
              Connect a LiteLLM proxy to route LLM calls through a central
              gateway with cost tracking, rate limiting, and model management.
            </p>

            <div className="grid grid-cols-1 gap-md mb-md">
              <FormField
                label="Proxy URL"
                hint="Base URL of your LiteLLM proxy instance"
              >
                <TextInput
                  value={proxyUrl}
                  onChange={(e) => setProxyUrl(e.target.value)}
                  placeholder="http://localhost:4000"
                  type="url"
                />
              </FormField>

              <FormField
                label="API Key"
                hint={
                  org?.litellm_api_key_set
                    ? 'A key is already stored. Enter a new value to replace it.'
                    : 'Master key for the proxy (optional)'
                }
              >
                <form autoComplete="off" onSubmit={(e) => e.preventDefault()}>
                  <input
                    type="text"
                    className="hidden"
                    tabIndex={-1}
                    aria-hidden="true"
                    readOnly
                  />
                  <input
                    type="password"
                    className="hidden"
                    tabIndex={-1}
                    aria-hidden="true"
                    readOnly
                  />
                  <TextInput
                    type="text"
                    value={proxyApiKey}
                    onChange={(e) => setProxyApiKey(e.target.value)}
                    placeholder={
                      org?.litellm_api_key_set ? '••••••••' : 'sk-...'
                    }
                    autoComplete="one-time-code"
                    name="infra_litellm_key"
                    data-1p-ignore
                    data-lpignore="true"
                    data-form-type="other"
                    className="password-mask"
                  />
                </form>
              </FormField>
            </div>

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={handleTestLiteLLM}
                disabled={testing || !proxyUrl.trim()}
              >
                {testing ? (
                  <>
                    <RefreshCw size={12} className="animate-spin" /> Testing...
                  </>
                ) : (
                  'Test Connection'
                )}
              </Button>
            </div>

            {testResult && (
              <div
                className={`mt-3 rounded-md px-3 py-2 text-xs ${
                  testResult.healthy
                    ? 'bg-success/10 text-success'
                    : 'bg-error/10 text-error'
                }`}
              >
                <div className="flex items-center gap-1.5 font-medium">
                  {testResult.healthy ? (
                    <Check size={12} />
                  ) : (
                    <X size={12} />
                  )}
                  {testResult.healthy
                    ? 'Connected'
                    : testResult.error || 'Connection failed'}
                </div>
                {testResult.healthy &&
                  testResult.models.length > 0 && (
                    <div className="mt-1.5 text-text-secondary">
                      <span className="font-medium">
                        {testResult.models.length} models:
                      </span>{' '}
                      {testResult.models.slice(0, 8).join(', ')}
                      {testResult.models.length > 8 &&
                        ` +${testResult.models.length - 8} more`}
                    </div>
                  )}
              </div>
            )}
          </Card>

          {/* ─── Vector Database (Milvus) ─── */}
          <Card className="mb-md">
            <div className="flex items-center gap-2 mb-md">
              <Database size={16} className="text-primary" />
              <h2 className="text-lg font-semibold m-0 font-[family-name:var(--font-heading)]">
                Vector Database (Milvus)
              </h2>
              {milvusUri && <Badge variant="info">Configured</Badge>}
            </div>
            <p className="text-sm text-text-muted mt-0 mb-md">
              Milvus stores vector embeddings for semantic search, RAG, and the
              Context Engine. Leave empty to use the default from environment
              variables.
            </p>

            <FormField
              label="Milvus URI"
              hint="Connection URI (e.g. http://localhost:19530 or a Zilliz endpoint)"
            >
              <TextInput
                value={milvusUri}
                onChange={(e) => setMilvusUri(e.target.value)}
                placeholder="http://localhost:19530"
                type="url"
              />
            </FormField>
          </Card>

          {/* ─── Graph Database (NebulaGraph) ─── */}
          <Card className="mb-md">
            <div className="flex items-center gap-2 mb-md">
              <Network size={16} className="text-primary" />
              <h2 className="text-lg font-semibold m-0 font-[family-name:var(--font-heading)]">
                Graph Database (NebulaGraph)
              </h2>
              {nebulaHost && nebulaHost !== 'localhost' && (
                <Badge variant="info">Custom</Badge>
              )}
            </div>
            <p className="text-sm text-text-muted mt-0 mb-md">
              NebulaGraph powers the knowledge graph for entity relations,
              temporal facts, and graph-based retrieval. Leave defaults for local
              development.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
              <FormField label="Host" hint="NebulaGraph server hostname">
                <TextInput
                  value={nebulaHost}
                  onChange={(e) => setNebulaHost(e.target.value)}
                  placeholder="localhost"
                />
              </FormField>

              <FormField label="Port" hint="NebulaGraph Thrift port">
                <TextInput
                  value={nebulaPort}
                  onChange={(e) => setNebulaPort(e.target.value)}
                  placeholder="9669"
                  type="number"
                />
              </FormField>
            </div>
          </Card>

          {/* ─── Save ─── */}
          <Button onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save Infrastructure Settings'}
          </Button>
        </>
      )}
    </div>
  );
}
