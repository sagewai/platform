'use client';

import { useEffect, useState, useCallback } from 'react';
import { PageLayout, Card, Button, Badge, FormField, TextInput, useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type { ProviderConfig, OllamaModelInfo, LMStudioModelInfo } from '@/utils/types';
import { ChevronDown, ChevronRight, Check, X, Zap, Server, Cloud, AlertCircle, ExternalLink, RefreshCw } from 'lucide-react';

/* ─── Provider Catalog ─── */

interface ProviderField {
  key: string;
  label: string;
  type: 'password' | 'text';
  placeholder: string;
  hint?: string;
}

interface ProviderDef {
  name: string;
  display: string;
  description: string;
  fields: ProviderField[];
  envVar?: string;
  docsUrl?: string;
  freetier?: boolean;
}

const CLOUD_PROVIDERS: ProviderDef[] = [
  {
    name: 'openai', display: 'OpenAI', envVar: 'SAGEWAI_OPENAI_API_KEY',
    description: 'GPT-4o, GPT-4.1, and GPT-4o-mini models.',
    docsUrl: 'https://platform.openai.com/api-keys',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-...', hint: 'From platform.openai.com' }],
  },
  {
    name: 'anthropic', display: 'Anthropic', envVar: 'SAGEWAI_ANTHROPIC_API_KEY',
    description: 'Claude Sonnet 4, Claude Haiku, and Claude Opus models.',
    docsUrl: 'https://console.anthropic.com/settings/keys',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-ant-...', hint: 'From console.anthropic.com' }],
  },
  {
    name: 'google', display: 'Google AI', envVar: 'SAGEWAI_GOOGLE_API_KEY', freetier: true,
    description: 'Gemini 2.0 Flash & Pro. Free tier available with generous limits.',
    docsUrl: 'https://aistudio.google.com/apikey',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: 'AIza...', hint: 'Free key from aistudio.google.com' }],
  },
  {
    name: 'mistral', display: 'Mistral AI', envVar: 'SAGEWAI_MISTRAL_API_KEY',
    description: 'Mistral Large and open-weight models.',
    docsUrl: 'https://console.mistral.ai/api-keys',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: '', hint: 'From console.mistral.ai' }],
  },
  {
    name: 'groq', display: 'Groq', envVar: 'SAGEWAI_GROQ_API_KEY', freetier: true,
    description: 'Ultra-fast inference for Llama, Mixtral. Free tier available.',
    docsUrl: 'https://console.groq.com/keys',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: 'gsk_...', hint: 'Free key from console.groq.com' }],
  },
  {
    name: 'together', display: 'Together AI', envVar: 'SAGEWAI_TOGETHER_API_KEY',
    description: 'Open-source models (Llama 3.1, CodeLlama, etc.) with fast inference.',
    docsUrl: 'https://api.together.xyz/settings/api-keys',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: '', hint: 'From api.together.xyz' }],
  },
  {
    name: 'xai', display: 'xAI', envVar: 'SAGEWAI_XAI_API_KEY',
    description: 'Grok-2 models.',
    docsUrl: 'https://console.x.ai',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: 'xai-...', hint: 'From console.x.ai' }],
  },
  {
    name: 'perplexity', display: 'Perplexity', envVar: 'SAGEWAI_PERPLEXITY_API_KEY',
    description: 'Search-augmented models with real-time web knowledge.',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: 'pplx-...', hint: 'From perplexity.ai' }],
  },
  {
    name: 'cohere', display: 'Cohere', envVar: 'SAGEWAI_COHERE_API_KEY',
    description: 'Command R+ and embedding models.',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', placeholder: '', hint: 'From dashboard.cohere.com' }],
  },
];

const LOCAL_PROVIDERS: ProviderDef[] = [
  {
    name: 'ollama', display: 'Ollama', freetier: true,
    description: 'Run open-source models locally. Install from ollama.com, then "ollama pull llama3.1".',
    docsUrl: 'https://ollama.com',
    fields: [{ key: 'endpoint', label: 'Endpoint', type: 'text', placeholder: 'http://localhost:11434', hint: 'Default: http://localhost:11434' }],
  },
  {
    name: 'lmstudio', display: 'LM Studio', freetier: true,
    description: 'Desktop app for running local models with an OpenAI-compatible API.',
    docsUrl: 'https://lmstudio.ai',
    fields: [{ key: 'endpoint', label: 'Endpoint', type: 'text', placeholder: 'http://localhost:1234/v1', hint: 'Start LM Studio server first' }],
  },
  {
    name: 'openai-compatible', display: 'OpenAI-Compatible',
    description: 'Any server that exposes the OpenAI API format (vLLM, TGI, llama.cpp server, etc.).',
    fields: [
      { key: 'endpoint', label: 'Endpoint URL', type: 'text', placeholder: 'http://localhost:8080/v1', hint: 'The base URL of your server' },
      { key: 'api_key', label: 'API Key (optional)', type: 'password', placeholder: '', hint: 'Leave empty if not required' },
    ],
  },
];

const ENTERPRISE_PROVIDERS: ProviderDef[] = [
  {
    name: 'azure-openai', display: 'Azure OpenAI',
    description: 'OpenAI models hosted on Azure with enterprise compliance.',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', placeholder: '' },
      { key: 'resource_name', label: 'Resource Name', type: 'text', placeholder: 'my-resource' },
      { key: 'deployment_name', label: 'Deployment', type: 'text', placeholder: 'gpt-4o' },
      { key: 'api_version', label: 'API Version', type: 'text', placeholder: '2024-02-01' },
    ],
  },
  {
    name: 'aws-bedrock', display: 'AWS Bedrock',
    description: 'Managed access to Claude, Llama, and other models on AWS.',
    fields: [
      { key: 'access_key', label: 'Access Key', type: 'password', placeholder: '' },
      { key: 'secret_key', label: 'Secret Key', type: 'password', placeholder: '' },
      { key: 'region', label: 'Region', type: 'text', placeholder: 'us-east-1' },
    ],
  },
  {
    name: 'vertex-ai', display: 'Vertex AI',
    description: 'Google Cloud managed inference with Gemini and other models.',
    fields: [
      { key: 'project_id', label: 'Project ID', type: 'text', placeholder: 'my-project' },
      { key: 'location', label: 'Location', type: 'text', placeholder: 'us-central1' },
      { key: 'service_account_json', label: 'Service Account JSON', type: 'password', placeholder: '{"type":"service_account",...}' },
    ],
  },
];

/* ─── Components ─── */

function StatusBadge({ status }: { status: 'connected' | 'env' | 'not_configured' }) {
  if (status === 'connected') return <Badge variant="success">Connected</Badge>;
  if (status === 'env') return <Badge variant="info">Via ENV</Badge>;
  return <Badge variant="default">Not configured</Badge>;
}

function formatSize(bytes: number): string {
  if (bytes === 0) return '';
  const gb = bytes / (1024 * 1024 * 1024);
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
}

type TestResult = { connected?: boolean; models?: string[]; error?: string; latency_ms?: number };

interface ProviderCardProps {
  provider: ProviderDef;
  configs: ProviderConfig[];
  onSave: (name: string, type: string, display: string, values: Record<string, string>) => Promise<void>;
  onTest: (id: string) => Promise<void>;
  testingId: string | null;
  testResults: Record<string, TestResult>;
}

function ProviderCard({ provider, configs, onSave, onTest, testingId, testResults }: ProviderCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [formValues, setFormValues] = useState<Record<string, string>>({});

  const cfg = configs.find((c) => c.provider_name === provider.name);
  const status: 'connected' | 'env' | 'not_configured' =
    cfg?.env_var_set ? 'env' : cfg?.status === 'configured' ? 'connected' : 'not_configured';

  const result = testResults[provider.name];

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-bg-surface">
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-transparent border-0 cursor-pointer text-left hover:bg-bg-subtle/50 transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          {expanded
            ? <ChevronDown size={14} className="text-text-muted shrink-0" />
            : <ChevronRight size={14} className="text-text-muted shrink-0" />
          }
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm text-text-primary">{provider.display}</span>
              {provider.freetier && <span className="text-[10px] font-medium text-success bg-success/10 px-1.5 py-0.5 rounded">FREE TIER</span>}
            </div>
            <p className="text-xs text-text-muted m-0 mt-0.5 truncate">{provider.description}</p>
          </div>
        </div>
        <div className="shrink-0">
          <StatusBadge status={status} />
        </div>
      </button>

      {/* Body */}
      {expanded && (
        <div className="border-t border-border px-4 py-3">
          {status === 'env' && provider.envVar && (
            <div className="text-xs text-text-muted bg-bg-subtle rounded-md px-3 py-2 mb-3 font-[family-name:var(--font-mono)] flex items-center gap-2">
              <Check size={12} className="text-success" />
              Set via <strong>{provider.envVar}</strong> environment variable
            </div>
          )}

          {/* Config form — wrapped in form with autoComplete off to prevent credential autofill */}
          {status !== 'env' && (
            <form autoComplete="off" onSubmit={(e) => e.preventDefault()} className="flex flex-col gap-2 mb-3">
              {/* Decoy inputs absorb browser autofill so real fields stay clean */}
              <input type="text" className="hidden" tabIndex={-1} aria-hidden="true" readOnly />
              <input type="password" className="hidden" tabIndex={-1} aria-hidden="true" readOnly />
              {provider.fields.map((field) => (
                <FormField key={field.key} label={field.label} hint={field.hint}>
                  <TextInput
                    type="text"
                    value={formValues[field.key] || ''}
                    onChange={(e) => setFormValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                    placeholder={field.placeholder}
                    autoComplete="one-time-code"
                    name={`llmcfg_${provider.name}_${field.key}`}
                    data-1p-ignore
                    data-lpignore="true"
                    data-form-type="other"
                    className={field.type === 'password' ? 'password-mask' : undefined}
                  />
                </FormField>
              ))}
              {provider.envVar && (
                <p className="text-xs text-text-muted m-0">
                  Or set <code className="font-[family-name:var(--font-mono)] text-[11px] bg-bg-subtle px-1 rounded">{provider.envVar}</code> in your <code className="font-[family-name:var(--font-mono)] text-[11px] bg-bg-subtle px-1 rounded">.env</code> file
                </p>
              )}
            </form>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2">
            {status !== 'env' && (
              <Button size="sm" onClick={() => onSave(provider.name, 'hosted', provider.display, formValues)}>
                Save
              </Button>
            )}
            {status !== 'not_configured' && (
              <Button size="sm" variant="secondary" onClick={() => onTest(provider.name)} disabled={testingId === provider.name}>
                {testingId === provider.name ? 'Testing...' : 'Test Connection'}
              </Button>
            )}
            {provider.docsUrl && (
              <a
                href={provider.docsUrl}
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
            <div className={`mt-3 rounded-md px-3 py-2 text-xs ${result.connected ? 'bg-success/10 text-success' : 'bg-error/10 text-error'}`}>
              <div className="flex items-center gap-1.5 font-medium">
                {result.connected ? <Check size={12} /> : <X size={12} />}
                {result.connected ? `Connected (${result.latency_ms}ms)` : result.error || 'Connection failed'}
              </div>
              {result.connected && result.models && result.models.length > 0 && (
                <div className="mt-1.5 text-text-secondary">
                  <span className="font-medium">{result.models.length} models:</span>{' '}
                  {result.models.slice(0, 8).join(', ')}
                  {result.models.length > 8 && ` +${result.models.length - 8} more`}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Main Page ─── */

export default function ModelsSettingsPage() {
  const [configs, setConfigs] = useState<ProviderConfig[]>([]);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const [ollamaModels, setOllamaModels] = useState<OllamaModelInfo[]>([]);
  const [ollamaConnected, setOllamaConnected] = useState<boolean | null>(null);
  const [ollamaLoading, setOllamaLoading] = useState(true);
  const [lmstudioModels, setLmstudioModels] = useState<LMStudioModelInfo[]>([]);
  const [lmstudioConnected, setLmstudioConnected] = useState<boolean | null>(null);
  const [lmstudioEndpoint, setLmstudioEndpoint] = useState('');
  const [lmstudioLoading, setLmstudioLoading] = useState(true);
  const { toast } = useToast();

  const loadConfigs = useCallback(async () => {
    try {
      const data = await adminApi.listProviderConfigs();
      setConfigs(data);
    } catch {
      // ignore
    }
  }, []);

  const loadOllama = useCallback(async () => {
    setOllamaLoading(true);
    try {
      const data = await adminApi.listOllamaModels();
      setOllamaConnected(data.connected);
      setOllamaModels(data.models || []);
    } catch {
      setOllamaConnected(false);
      setOllamaModels([]);
    } finally {
      setOllamaLoading(false);
    }
  }, []);

  const loadLmstudio = useCallback(async () => {
    setLmstudioLoading(true);
    try {
      const data = await adminApi.listLMStudioModels();
      setLmstudioConnected(data.connected);
      setLmstudioModels(data.models || []);
      if (data.endpoint) setLmstudioEndpoint(data.endpoint);
    } catch {
      setLmstudioConnected(false);
      setLmstudioModels([]);
    } finally {
      setLmstudioLoading(false);
    }
  }, []);

  useEffect(() => { loadConfigs(); loadOllama(); loadLmstudio(); }, [loadConfigs, loadOllama, loadLmstudio]);

  const handleSave = async (name: string, type: string, display: string, values: Record<string, string>) => {
    try {
      await adminApi.upsertProviderConfig({
        provider_name: name,
        provider_type: type,
        display_name: display,
        config: values,
      });
      toast('success', `${display} configured`);
      await loadConfigs();
    } catch {
      toast('error', 'Failed to save provider');
    }
  };

  const handleTest = async (id: string) => {
    setTestingId(id);
    try {
      const result = await adminApi.testProviderConfig(id);
      setTestResults((prev) => ({ ...prev, [id]: result }));
      if (result.connected) toast('success', `Connected (${result.latency_ms}ms)`);
      else toast('error', result.error || 'Connection failed');
    } catch {
      toast('error', 'Test failed');
    } finally {
      setTestingId(null);
    }
  };

  const configuredCount = configs.filter((c) => c.status === 'configured' || c.env_var_set).length + (ollamaConnected ? 1 : 0) + (lmstudioConnected ? 1 : 0);

  return (
    <PageLayout
      title="AI Models & Providers"
      description="Connect LLM providers to power your agents. You need at least one configured provider."
    >
      {/* Status summary */}
      {configuredCount === 0 && (
        <Card className="!bg-warning/5 border-warning/20 mb-lg">
          <div className="flex items-start gap-3">
            <AlertCircle size={18} className="text-warning shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-sm text-text-primary m-0">No providers configured</p>
              <p className="text-sm text-text-secondary m-0 mt-1">
                Your agents need an LLM provider to work. The fastest way to get started:
              </p>
              <ul className="text-sm text-text-secondary mt-2 mb-0 pl-4">
                <li><strong>Free &amp; instant:</strong> Get a Google AI key from aistudio.google.com (Gemini 2.0 Flash)</li>
                <li><strong>Free &amp; local:</strong> Install Ollama and run <code className="font-[family-name:var(--font-mono)] text-[11px] bg-bg-subtle px-1 rounded">ollama pull llama3.1</code></li>
                <li><strong>Fast &amp; free tier:</strong> Get a Groq key from console.groq.com</li>
              </ul>
            </div>
          </div>
        </Card>
      )}

      {/* ─── Section 1: Local Inference ─── */}
      <section className="mb-xl">
        <div className="flex items-center gap-2 mb-sm">
          <Server size={16} className="text-primary" />
          <h2 className="text-lg font-semibold m-0 font-[family-name:var(--font-heading)]">Local Inference</h2>
          <span className="text-[10px] font-medium text-success bg-success/10 px-1.5 py-0.5 rounded">FREE</span>
        </div>
        <p className="text-sm text-text-muted mt-0 mb-md">
          Run models on your own hardware. No API keys, no usage costs, full data privacy.
        </p>

        {/* Ollama special section */}
        <Card className="mb-md">
          <div className="flex items-center justify-between mb-sm">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm">Ollama</span>
              {ollamaConnected === true && <Badge variant="success">Running</Badge>}
              {ollamaConnected === false && <Badge variant="default">Not detected</Badge>}
              {ollamaConnected === null && <Badge variant="default">Checking...</Badge>}
            </div>
            <Button size="sm" variant="ghost" onClick={loadOllama} disabled={ollamaLoading}>
              <RefreshCw size={12} className={ollamaLoading ? 'animate-spin' : ''} /> Refresh
            </Button>
          </div>

          {ollamaConnected ? (
            <>
              {ollamaModels.length > 0 ? (
                <div className="border border-border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-bg-subtle">
                        <th className="text-left py-2 px-3 text-xs text-text-muted uppercase tracking-wide font-medium">Model</th>
                        <th className="text-left py-2 px-3 text-xs text-text-muted uppercase tracking-wide font-medium">Size</th>
                        <th className="text-left py-2 px-3 text-xs text-text-muted uppercase tracking-wide font-medium">Params</th>
                        <th className="text-left py-2 px-3 text-xs text-text-muted uppercase tracking-wide font-medium">Quantization</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ollamaModels.map((m) => (
                        <tr key={m.name} className="border-t border-border">
                          <td className="py-2 px-3 font-medium font-[family-name:var(--font-mono)] text-xs">{m.name}</td>
                          <td className="py-2 px-3 text-xs text-text-muted">{formatSize(m.size)}</td>
                          <td className="py-2 px-3 text-xs text-text-muted">{m.parameter_size || '—'}</td>
                          <td className="py-2 px-3 text-xs text-text-muted">{m.quantization || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-sm text-text-muted bg-bg-subtle rounded-lg px-4 py-3">
                  Ollama is running but has no models installed. Run:{' '}
                  <code className="font-[family-name:var(--font-mono)] text-[11px] bg-bg-deep px-1.5 py-0.5 rounded">ollama pull llama3.1</code>
                </div>
              )}
              <p className="text-xs text-text-muted mt-2 mb-0">
                Models are available as <code className="font-[family-name:var(--font-mono)] text-[11px]">ollama/model-name</code> in agent configuration.
              </p>
            </>
          ) : (
            <div className="text-sm text-text-muted bg-bg-subtle rounded-lg px-4 py-3">
              <p className="m-0">Ollama is not running on <code className="font-[family-name:var(--font-mono)] text-[11px]">localhost:11434</code>.</p>
              <p className="m-0 mt-2">
                To get started:{' '}
                <a href="https://ollama.com" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                  Install Ollama
                </a>
                {' '}&rarr; run <code className="font-[family-name:var(--font-mono)] text-[11px] bg-bg-deep px-1.5 py-0.5 rounded">ollama serve</code>
                {' '}&rarr; <code className="font-[family-name:var(--font-mono)] text-[11px] bg-bg-deep px-1.5 py-0.5 rounded">ollama pull llama3.1</code>
              </p>
            </div>
          )}
        </Card>

        {/* LM Studio special section */}
        <Card className="mb-md">
          <div className="flex items-center justify-between mb-sm">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm">LM Studio</span>
              {lmstudioConnected === true && <Badge variant="success">Running</Badge>}
              {lmstudioConnected === false && <Badge variant="default">Not detected</Badge>}
              {lmstudioConnected === null && <Badge variant="default">Checking...</Badge>}
            </div>
            <Button size="sm" variant="ghost" onClick={loadLmstudio} disabled={lmstudioLoading}>
              <RefreshCw size={12} className={lmstudioLoading ? 'animate-spin' : ''} /> Refresh
            </Button>
          </div>

          {lmstudioConnected ? (
            <>
              {lmstudioModels.length > 0 ? (
                <div className="border border-border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-bg-subtle">
                        <th className="text-left py-2 px-3 text-xs text-text-muted uppercase tracking-wide font-medium">Model</th>
                        <th className="text-left py-2 px-3 text-xs text-text-muted uppercase tracking-wide font-medium">Owner</th>
                      </tr>
                    </thead>
                    <tbody>
                      {lmstudioModels.map((m) => (
                        <tr key={m.id} className="border-t border-border">
                          <td className="py-2 px-3 font-medium font-[family-name:var(--font-mono)] text-xs">{m.id}</td>
                          <td className="py-2 px-3 text-xs text-text-muted">{m.owned_by || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-sm text-text-muted bg-bg-subtle rounded-lg px-4 py-3">
                  LM Studio is running but has no models loaded. Load a model in the LM Studio app first.
                </div>
              )}
              <p className="text-xs text-text-muted mt-2 mb-0">
                Endpoint: <code className="font-[family-name:var(--font-mono)] text-[11px]">{lmstudioEndpoint || 'http://localhost:1234/v1'}</code>
                {' '}— Models are available as <code className="font-[family-name:var(--font-mono)] text-[11px]">openai/model-id</code> with <code className="font-[family-name:var(--font-mono)] text-[11px]">api_base</code> set to the endpoint.
              </p>
            </>
          ) : (
            <div className="text-sm text-text-muted bg-bg-subtle rounded-lg px-4 py-3">
              <p className="m-0">LM Studio server is not running on <code className="font-[family-name:var(--font-mono)] text-[11px]">localhost:1234</code>.</p>
              <p className="m-0 mt-2">
                To get started:{' '}
                <a href="https://lmstudio.ai" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">
                  Install LM Studio
                </a>
                {' '}&rarr; download a model &rarr; go to the <strong>Developer</strong> tab &rarr; start the local server.
              </p>
            </div>
          )}
        </Card>

        {/* Other local providers (OpenAI-compatible) */}
        <div className="flex flex-col gap-2">
          {LOCAL_PROVIDERS.filter((p) => p.name !== 'ollama' && p.name !== 'lmstudio').map((provider) => (
            <ProviderCard
              key={provider.name}
              provider={provider}
              configs={configs}
              onSave={handleSave}
              onTest={handleTest}
              testingId={testingId}
              testResults={testResults}
            />
          ))}
        </div>
      </section>

      {/* ─── Section 2: Cloud API Providers ─── */}
      <section className="mb-xl">
        <div className="flex items-center gap-2 mb-sm">
          <Cloud size={16} className="text-primary" />
          <h2 className="text-lg font-semibold m-0 font-[family-name:var(--font-heading)]">Cloud API Providers</h2>
        </div>
        <p className="text-sm text-text-muted mt-0 mb-md">
          Connect to hosted LLM APIs. Each provider requires an API key. Models are auto-discovered after connecting.
        </p>
        <div className="flex flex-col gap-2">
          {CLOUD_PROVIDERS.map((provider) => (
            <ProviderCard
              key={provider.name}
              provider={provider}
              configs={configs}
              onSave={handleSave}
              onTest={handleTest}
              testingId={testingId}
              testResults={testResults}
            />
          ))}
        </div>
      </section>

      {/* ─── Section 3: Enterprise Cloud ─── */}
      <section>
        <div className="flex items-center gap-2 mb-sm">
          <Zap size={16} className="text-primary" />
          <h2 className="text-lg font-semibold m-0 font-[family-name:var(--font-heading)]">Enterprise Cloud</h2>
        </div>
        <p className="text-sm text-text-muted mt-0 mb-md">
          Managed cloud inference with enterprise compliance, SLAs, and dedicated capacity.
        </p>
        <div className="flex flex-col gap-2">
          {ENTERPRISE_PROVIDERS.map((provider) => (
            <ProviderCard
              key={provider.name}
              provider={provider}
              configs={configs}
              onSave={handleSave}
              onTest={handleTest}
              testingId={testingId}
              testResults={testResults}
            />
          ))}
        </div>
      </section>
    </PageLayout>
  );
}
