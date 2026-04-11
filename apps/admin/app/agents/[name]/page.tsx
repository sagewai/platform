'use client';

import { adminApi } from '@/utils/api';
import type { AgentDetail, AvailableModel, PromptLogSummary, ConnectorCatalogItem } from '@/utils/types';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState, use } from 'react';
import { Card, Badge, Button, FormField, TextInput, TextArea, Select, Tabs, Skeleton, useToast } from '@/components/ui/legacy';
import { AlertCircle, Bookmark, ChevronDown, ChevronRight, MessageSquare, Tag, X, Plus, Pencil, Trash2, Plug } from 'lucide-react';
import { SSEChat } from '@/components/sse-chat';
import { AgentMemoryConfigPanel } from '@/components/agent-memory-config';

interface Props {
  params: Promise<{ name: string }>;
}

const STRATEGY_OPTIONS = ['react', 'lats', 'tree_of_thoughts', 'self_correction'];

export default function AgentDetailPage({ params }: Props) {
  const { name: rawName } = use(params);
  const name = decodeURIComponent(rawName);
  const router = useRouter();
  const [agent, setAgent] = useState<AgentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');
  const { toast } = useToast();

  // Rename state
  const [renaming, setRenaming] = useState(false);
  const [renameTo, setRenameTo] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);

  // Delete state
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const [editing, setEditing] = useState(false);
  const [editModel, setEditModel] = useState('');
  const [editTemp, setEditTemp] = useState(0.7);
  const [editTopP, setEditTopP] = useState<number | null>(null);
  const [editMaxTokens, setEditMaxTokens] = useState<number | null>(null);
  const [editFreqPenalty, setEditFreqPenalty] = useState<number | null>(null);
  const [editPresPenalty, setEditPresPenalty] = useState<number | null>(null);
  const [editMaxIter, setEditMaxIter] = useState(10);
  const [editStrategy, setEditStrategy] = useState('');
  const [editSystemPrompt, setEditSystemPrompt] = useState('');
  const [editTags, setEditTags] = useState<string[]>([]);
  const [editFallbackModels, setEditFallbackModels] = useState<string[]>([]);
  const [newTag, setNewTag] = useState('');
  const [saving, setSaving] = useState(false);
  const [savingTags, setSavingTags] = useState(false);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [connectors, setConnectors] = useState<ConnectorCatalogItem[]>([]);
  const [editMcpServers, setEditMcpServers] = useState<string[]>([]);
  const [examples, setExamples] = useState<PromptLogSummary[]>([]);

  function populateEditFields(data: AgentDetail) {
    setEditModel(data.model || '');
    setEditTemp(data.temperature ?? 0.7);
    setEditTopP(data.top_p ?? null);
    setEditMaxTokens(data.max_tokens ?? null);
    setEditFreqPenalty(data.frequency_penalty ?? null);
    setEditPresPenalty(data.presence_penalty ?? null);
    setEditMaxIter(data.max_iterations ?? 10);
    setEditStrategy(data.strategy || '');
    setEditSystemPrompt(data.system_prompt || '');
    setEditTags(data.tags ?? []);
    setEditFallbackModels(data.fallback_models ?? []);
    setEditMcpServers(data.mcp_servers ?? []);
  }

  async function fetchAgent() {
    try {
      const data = await adminApi.getAgent(name);
      setAgent(data);
      populateEditFields(data);
    } catch {
      setAgent(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchAgent(); }, [name]);
  useEffect(() => { adminApi.listAvailableModels().then(setAvailableModels).catch(() => {}); }, []);
  useEffect(() => { adminApi.listConnectors().then(setConnectors).catch(() => {}); }, []);
  useEffect(() => { adminApi.listExamples(name).then(setExamples).catch(() => {}); }, [name]);

  async function handleSave() {
    setSaving(true);
    try {
      const config: Record<string, unknown> = {};
      if (editModel) config.model = editModel;
      config.temperature = editTemp;
      if (editTopP !== null) config.top_p = editTopP;
      if (editMaxTokens !== null) config.max_tokens = editMaxTokens;
      if (editFreqPenalty !== null) config.frequency_penalty = editFreqPenalty;
      if (editPresPenalty !== null) config.presence_penalty = editPresPenalty;
      config.max_iterations = editMaxIter;
      if (editStrategy) config.strategy = editStrategy;
      if (editSystemPrompt) config.system_prompt = editSystemPrompt;
      config.tags = editTags;
      config.fallback_models = editFallbackModels;
      config.mcp_servers = editMcpServers;

      await adminApi.updateAgentConfig(name, config);
      toast('success', 'Configuration saved');
      setEditing(false);
      await fetchAgent();
    } catch (e) {
      toast('error', `Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleRename() {
    const trimmed = renameTo.trim();
    if (!trimmed || trimmed === name) return;
    setRenameBusy(true);
    try {
      await adminApi.renameAgent(name, trimmed);
      toast('success', `Renamed to "${trimmed}"`);
      router.push(`/agents/${encodeURIComponent(trimmed)}`);
    } catch (e) {
      toast('error', e instanceof Error ? e.message : 'Rename failed');
    } finally {
      setRenameBusy(false);
    }
  }

  async function handleDelete() {
    setDeleteBusy(true);
    try {
      await adminApi.deleteAgent(name);
      toast('success', `Agent "${name}" deleted`);
      router.push('/agents');
    } catch (e) {
      toast('error', e instanceof Error ? e.message : 'Delete failed');
    } finally {
      setDeleteBusy(false);
      setShowDeleteConfirm(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto">
        <Skeleton lines={1} className="w-32 mb-md" />
        <Skeleton lines={1} className="w-64 mb-lg" />
        <div className="grid grid-cols-2 gap-md"><Skeleton lines={2} /><Skeleton lines={2} /></div>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="max-w-4xl mx-auto">
        <Link href="/agents" className="text-primary no-underline text-sm">&larr; Back to agents</Link>
        <h1 className="mt-md text-xl font-bold font-[family-name:var(--font-heading)]">Agent not found</h1>
        <p className="text-text-muted text-sm mt-sm">
          The agent <strong>{decodeURIComponent(name)}</strong> is not in the registry.
          {' '}It may need to be created first via the{' '}
          <Link href="/playground" className="text-primary hover:underline">Playground</Link>.
        </p>
      </div>
    );
  }

  const tabs = [
    { id: 'overview', label: 'Overview' },
    ...(agent.source === 'playground' ? [{ id: 'chat', label: 'Chat' }] : []),
    { id: 'config', label: 'Configuration' },
    { id: 'memory', label: 'Memory & Context' },
    { id: 'runs', label: `Runs (${agent.total_runs})` },
  ];

  return (
    <div className="max-w-4xl mx-auto">
      <Link href="/agents" className="text-primary no-underline text-sm">&larr; Back to agents</Link>
      <div className="flex items-center gap-md mt-md mb-lg flex-wrap">
        {renaming ? (
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={renameTo}
              onChange={(e) => setRenameTo(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleRename(); if (e.key === 'Escape') setRenaming(false); }}
              placeholder="New agent name"
              autoFocus
              className="px-2.5 py-1.5 rounded-md border border-border text-sm bg-bg-surface outline-none focus:border-primary font-[family-name:var(--font-heading)] font-bold"
            />
            <Button onClick={handleRename} disabled={renameBusy || !renameTo.trim() || renameTo.trim() === name}>
              {renameBusy ? 'Renaming...' : 'Rename'}
            </Button>
            <Button variant="ghost" onClick={() => setRenaming(false)}>Cancel</Button>
          </div>
        ) : (
          <>
            <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">{agent.name}</h1>
            {agent.source === 'playground' && (
              <button
                type="button"
                onClick={() => { setRenameTo(agent.name); setRenaming(true); }}
                className="p-1 rounded bg-transparent border-none cursor-pointer text-text-muted hover:text-primary transition-colors"
                title="Rename agent"
              >
                <Pencil size={14} />
              </button>
            )}
          </>
        )}
        {agent.source === 'playground' && (
          <span className="text-[10px] font-semibold uppercase tracking-wider bg-secondary/15 text-secondary px-1.5 py-0.5 rounded">
            playground
          </span>
        )}
        <span className={`inline-flex items-center gap-1.5 text-sm ${agent.status === 'active' ? 'text-success' : 'text-text-muted'}`}>
          <span className={`w-2 h-2 rounded-full ${agent.status === 'active' ? 'bg-success' : 'bg-text-muted'}`} />
          {agent.status}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {agent.source === 'playground' && (
            <>
              <button
                type="button"
                onClick={() => setActiveTab('chat')}
                className="flex items-center gap-1 text-[12px] text-primary bg-transparent border-none cursor-pointer hover:underline"
              >
                <MessageSquare size={12} />
                Chat with agent
              </button>
              <button
                type="button"
                onClick={() => setShowDeleteConfirm(true)}
                className="flex items-center gap-1 text-[12px] text-error border border-error/30 bg-transparent rounded-md px-2 py-1 cursor-pointer hover:bg-error/10 transition-colors"
                title="Delete agent"
              >
                <Trash2 size={12} />
                Delete
              </button>
            </>
          )}
        </div>
      </div>

      {/* Delete confirmation dialog */}
      {showDeleteConfirm && (
        <div className="mb-md p-md rounded-lg border border-error/30 bg-error/5">
          <p className="text-sm text-text-primary m-0 mb-sm">
            Are you sure you want to delete <strong>{agent.name}</strong>? This action cannot be undone.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleteBusy}
              className="px-3 py-1.5 rounded-md bg-error text-white text-[13px] font-medium border-none cursor-pointer hover:opacity-90 disabled:opacity-50"
            >
              {deleteBusy ? 'Deleting...' : 'Yes, delete'}
            </button>
            <Button variant="ghost" onClick={() => setShowDeleteConfirm(false)}>Cancel</Button>
          </div>
        </div>
      )}

      {/* Tags — inline editable */}
      <div className="flex items-center gap-1.5 mb-md flex-wrap">
        <Tag size={12} className="text-text-muted shrink-0" />
        {editTags.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 text-[11px] bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium"
          >
            {tag}
            <button
              type="button"
              onClick={async () => {
                const updated = editTags.filter((t) => t !== tag);
                setEditTags(updated);
                setSavingTags(true);
                try {
                  await adminApi.updateAgentConfig(name, { tags: updated });
                  setAgent((a) => a ? { ...a, tags: updated } : a);
                } catch { /* ignore */ }
                setSavingTags(false);
              }}
              className="p-0 bg-transparent border-none cursor-pointer text-primary/60 hover:text-primary"
            >
              <X size={10} />
            </button>
          </span>
        ))}
        <form
          className="inline-flex items-center gap-1"
          onSubmit={async (e) => {
            e.preventDefault();
            const tag = newTag.trim().toLowerCase().replace(/\s+/g, '-');
            if (!tag || editTags.includes(tag)) return;
            const updated = [...editTags, tag];
            setEditTags(updated);
            setNewTag('');
            setSavingTags(true);
            try {
              await adminApi.updateAgentConfig(name, { tags: updated });
              setAgent((a) => a ? { ...a, tags: updated } : a);
            } catch { /* ignore */ }
            setSavingTags(false);
          }}
        >
          <input
            type="text"
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            placeholder="Add tag..."
            className="w-[80px] px-1.5 py-0.5 text-[11px] rounded border border-border/50 bg-transparent outline-none focus:border-primary focus:w-[120px] transition-all"
          />
          {newTag.trim() && (
            <button
              type="submit"
              disabled={savingTags}
              className="p-0.5 rounded bg-primary/10 text-primary border-none cursor-pointer hover:bg-primary/20"
            >
              <Plus size={10} />
            </button>
          )}
        </form>
        {savingTags && <span className="text-[10px] text-text-muted animate-pulse">saving...</span>}
      </div>

      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />

      {activeTab === 'overview' && (
        <>
          {/* Key metrics row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-md mb-lg">
            <Card className="!p-md">
              <div className="text-xs text-text-muted uppercase">Model</div>
              <div className="text-sm font-semibold mt-1 font-[family-name:var(--font-mono)] truncate" title={agent.model}>{agent.model || '\u2014'}</div>
            </Card>
            <Card className="!p-md">
              <div className="text-xs text-text-muted uppercase">Strategy</div>
              <div className="text-sm font-semibold mt-1 font-[family-name:var(--font-mono)]">{agent.strategy || 'react'}</div>
            </Card>
            <Card className="!p-md">
              <div className="text-xs text-text-muted uppercase">Total Runs</div>
              <div className="text-sm font-semibold mt-1">{agent.total_runs}</div>
            </Card>
            <Card className="!p-md">
              <div className="text-xs text-text-muted uppercase">Preset</div>
              <div className="text-sm font-semibold mt-1">{agent.preset || 'custom'}</div>
            </Card>
          </div>

          {/* Inference parameters */}
          <Card className="mb-md">
            <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">Inference Parameters</h3>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-md">
              <div>
                <div className="text-xs text-text-muted">Temperature</div>
                <div className="text-sm font-semibold mt-0.5 font-[family-name:var(--font-mono)]">{agent.temperature ?? '\u2014'}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted">Top P</div>
                <div className="text-sm font-semibold mt-0.5 font-[family-name:var(--font-mono)]">{agent.top_p ?? '\u2014'}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted">Max Tokens</div>
                <div className="text-sm font-semibold mt-0.5 font-[family-name:var(--font-mono)]">{agent.max_tokens ?? '\u2014'}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted">Frequency Penalty</div>
                <div className="text-sm font-semibold mt-0.5 font-[family-name:var(--font-mono)]">{agent.frequency_penalty ?? '\u2014'}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted">Presence Penalty</div>
                <div className="text-sm font-semibold mt-0.5 font-[family-name:var(--font-mono)]">{agent.presence_penalty ?? '\u2014'}</div>
              </div>
            </div>
            <div className="mt-md">
              <div className="text-xs text-text-muted">Max Iterations</div>
              <div className="text-sm font-semibold mt-0.5">{agent.max_iterations}</div>
            </div>
            {agent.fallback_models && agent.fallback_models.length > 0 && (
              <div className="mt-md col-span-full">
                <div className="text-xs text-text-muted mb-1">Fallback Chain</div>
                <div className="flex items-center gap-1 flex-wrap">
                  <span className="text-xs font-[family-name:var(--font-mono)] bg-primary/10 text-primary px-2 py-0.5 rounded">{agent.model}</span>
                  {agent.fallback_models.map((m) => (
                    <span key={m} className="flex items-center gap-1">
                      <span className="text-text-muted text-xs">&rarr;</span>
                      <span className="text-xs font-[family-name:var(--font-mono)] bg-bg-subtle px-2 py-0.5 rounded">{m}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </Card>

          {agent.capabilities.length > 0 && (
            <Card className="mb-md">
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">Capabilities</h3>
              <div className="flex flex-wrap gap-sm">
                {agent.capabilities.map((c) => (
                  <Badge key={c} variant="info">{c}</Badge>
                ))}
              </div>
            </Card>
          )}

          {/* Tools, MCP, Memory, Guardrails in a grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-md mb-md">
            <Card>
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">Tools ({agent.tools.length})</h3>
              {agent.tools.length > 0 ? (
                <div className="flex flex-wrap gap-sm">
                  {agent.tools.map((t) => (
                    <Badge key={t} variant="default">{t}</Badge>
                  ))}
                </div>
              ) : (
                <p className="text-text-muted text-xs m-0">No tools configured</p>
              )}
            </Card>
            <Card>
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">MCP Servers ({agent.mcp_servers?.length ?? 0})</h3>
              {agent.mcp_servers?.length > 0 ? (
                <div className="flex flex-wrap gap-sm">
                  {agent.mcp_servers.map((s) => (
                    <Badge key={s} variant="info">{s}</Badge>
                  ))}
                </div>
              ) : (
                <p className="text-text-muted text-xs m-0">No MCP servers connected</p>
              )}
            </Card>
            <Card>
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">Memory Backends ({agent.memory_backends?.length ?? 0})</h3>
              {agent.memory_backends?.length > 0 ? (
                <div className="flex flex-wrap gap-sm">
                  {agent.memory_backends.map((m) => (
                    <Badge key={m} variant="default">{m}</Badge>
                  ))}
                </div>
              ) : (
                <p className="text-text-muted text-xs m-0">No memory configured</p>
              )}
            </Card>
            <Card>
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">Guardrails ({agent.guardrails?.length ?? 0})</h3>
              {agent.guardrails?.length > 0 ? (
                <div className="flex flex-wrap gap-sm">
                  {agent.guardrails.map((g) => (
                    <Badge key={g} variant="warning">{g}</Badge>
                  ))}
                </div>
              ) : (
                <p className="text-text-muted text-xs m-0">No guardrails active</p>
              )}
            </Card>
          </div>

          {agent.system_prompt && (
            <Card>
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">System Prompt</h3>
              <pre className="bg-bg-subtle p-md rounded-md text-xs whitespace-pre-wrap font-[family-name:var(--font-mono)] m-0">
                {agent.system_prompt}
              </pre>
            </Card>
          )}
        </>
      )}

      {activeTab === 'chat' && agent.source === 'playground' && (
        <div className="h-[calc(100vh-220px)] min-h-[400px]">
          <SSEChat agentName={agent.name} />
        </div>
      )}

      {activeTab === 'config' && (
        <>
        <Card>
          {!editing ? (
            <div>
              <p className="text-sm text-text-secondary mb-md">
                Modify the agent&apos;s runtime configuration. Changes take effect immediately for subsequent runs.
              </p>
              <Button variant="secondary" onClick={() => setEditing(true)}>Edit Configuration</Button>
            </div>
          ) : (
            <div className="flex flex-col gap-md max-w-[36rem]">
              {/* Model */}
              <FormField label="Model">
                {availableModels.length > 0 ? (
                  <Select value={editModel} onChange={(e) => setEditModel(e.target.value)}>
                    <option value="">Use system default</option>
                    {Object.entries(
                      availableModels.reduce<Record<string, AvailableModel[]>>((acc, m) => {
                        (acc[m.provider] ??= []).push(m);
                        return acc;
                      }, {}),
                    ).map(([provider, models]) => (
                      <optgroup key={provider} label={provider.charAt(0).toUpperCase() + provider.slice(1)}>
                        {models.map((m) => (
                          <option key={m.id} value={m.id}>{m.id}</option>
                        ))}
                      </optgroup>
                    ))}
                  </Select>
                ) : (
                  <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2.5">
                    <AlertCircle size={14} className="text-warning shrink-0 mt-0.5" />
                    <div className="text-[12px]">
                      <p className="text-text-secondary m-0">No LLM providers configured.</p>
                      <p className="text-text-muted m-0 mt-1">
                        Set up a provider in{' '}
                        <Link href="/settings/models" className="text-primary hover:underline">
                          Settings &rarr; AI Models
                        </Link>
                      </p>
                    </div>
                  </div>
                )}
              </FormField>

              {/* Strategy */}
              <FormField label="Strategy">
                <Select value={editStrategy} onChange={(e) => setEditStrategy(e.target.value)}>
                  {STRATEGY_OPTIONS.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </Select>
              </FormField>

              {/* Temperature */}
              <FormField label={`Temperature: ${editTemp.toFixed(2)}`}>
                <input
                  type="range" min={0} max={2} step={0.05}
                  value={editTemp}
                  onChange={(e) => setEditTemp(parseFloat(e.target.value))}
                  className="w-full"
                />
                <div className="flex justify-between text-[10px] text-text-muted mt-0.5">
                  <span>Deterministic (0)</span>
                  <span>Creative (2)</span>
                </div>
              </FormField>

              {/* Top P */}
              <FormField label={`Top P: ${editTopP !== null ? editTopP.toFixed(2) : 'default'}`}>
                <div className="flex items-center gap-2">
                  <input
                    type="range" min={0} max={1} step={0.05}
                    value={editTopP ?? 1}
                    onChange={(e) => setEditTopP(parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <button
                    type="button"
                    onClick={() => setEditTopP(null)}
                    className="text-[10px] text-text-muted hover:text-primary border border-border rounded px-1.5 py-0.5 bg-transparent cursor-pointer"
                  >
                    Reset
                  </button>
                </div>
              </FormField>

              {/* Max Tokens */}
              <FormField label="Max Tokens">
                <div className="flex items-center gap-2">
                  <TextInput
                    type="number" min={1} max={128000}
                    value={editMaxTokens !== null ? String(editMaxTokens) : ''}
                    onChange={(e) => setEditMaxTokens(e.target.value ? parseInt(e.target.value, 10) : null)}
                    placeholder="Default (model limit)"
                  />
                  {editMaxTokens !== null && (
                    <button
                      type="button"
                      onClick={() => setEditMaxTokens(null)}
                      className="text-[10px] text-text-muted hover:text-primary border border-border rounded px-1.5 py-0.5 bg-transparent cursor-pointer shrink-0"
                    >
                      Reset
                    </button>
                  )}
                </div>
              </FormField>

              {/* Frequency Penalty */}
              <FormField label={`Frequency Penalty: ${editFreqPenalty !== null ? editFreqPenalty.toFixed(2) : 'default'}`}>
                <div className="flex items-center gap-2">
                  <input
                    type="range" min={-2} max={2} step={0.1}
                    value={editFreqPenalty ?? 0}
                    onChange={(e) => setEditFreqPenalty(parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <button
                    type="button"
                    onClick={() => setEditFreqPenalty(null)}
                    className="text-[10px] text-text-muted hover:text-primary border border-border rounded px-1.5 py-0.5 bg-transparent cursor-pointer"
                  >
                    Reset
                  </button>
                </div>
              </FormField>

              {/* Presence Penalty */}
              <FormField label={`Presence Penalty: ${editPresPenalty !== null ? editPresPenalty.toFixed(2) : 'default'}`}>
                <div className="flex items-center gap-2">
                  <input
                    type="range" min={-2} max={2} step={0.1}
                    value={editPresPenalty ?? 0}
                    onChange={(e) => setEditPresPenalty(parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <button
                    type="button"
                    onClick={() => setEditPresPenalty(null)}
                    className="text-[10px] text-text-muted hover:text-primary border border-border rounded px-1.5 py-0.5 bg-transparent cursor-pointer"
                  >
                    Reset
                  </button>
                </div>
              </FormField>

              {/* Max Iterations */}
              <FormField label="Max Iterations">
                <TextInput
                  type="number" min={1} max={100}
                  value={String(editMaxIter)}
                  onChange={(e) => setEditMaxIter(parseInt(e.target.value, 10) || 1)}
                />
              </FormField>

              {/* System Prompt */}
              <FormField label="System Prompt">
                <TextArea
                  value={editSystemPrompt}
                  onChange={(e) => setEditSystemPrompt(e.target.value)}
                  className="font-[family-name:var(--font-mono)] min-h-[120px]"
                />
              </FormField>

              {/* Fallback Models */}
              <FormField label="Fallback Models">
                <p className="text-[11px] text-text-muted mt-0 mb-2">
                  Ordered fallback chain — tried in sequence on timeout, rate-limit, or API error.
                </p>
                {editFallbackModels.length > 0 && (
                  <div className="flex flex-col gap-1.5 mb-2">
                    {editFallbackModels.map((m, i) => (
                      <div key={`${m}-${i}`} className="flex items-center gap-1.5 bg-bg-subtle rounded px-2 py-1.5">
                        <span className="text-[10px] text-text-muted w-4 shrink-0">#{i + 1}</span>
                        <span className="text-xs font-[family-name:var(--font-mono)] flex-1 truncate">{m}</span>
                        <button
                          type="button"
                          disabled={i === 0}
                          onClick={() => {
                            const arr = [...editFallbackModels];
                            [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
                            setEditFallbackModels(arr);
                          }}
                          className="text-[10px] text-text-muted hover:text-primary disabled:opacity-30 border-none bg-transparent cursor-pointer p-0.5"
                          title="Move up"
                        >
                          ▲
                        </button>
                        <button
                          type="button"
                          disabled={i === editFallbackModels.length - 1}
                          onClick={() => {
                            const arr = [...editFallbackModels];
                            [arr[i], arr[i + 1]] = [arr[i + 1], arr[i]];
                            setEditFallbackModels(arr);
                          }}
                          className="text-[10px] text-text-muted hover:text-primary disabled:opacity-30 border-none bg-transparent cursor-pointer p-0.5"
                          title="Move down"
                        >
                          ▼
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditFallbackModels(editFallbackModels.filter((_, j) => j !== i))}
                          className="text-[10px] text-error/70 hover:text-error border-none bg-transparent cursor-pointer p-0.5"
                          title="Remove"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {availableModels.length > 0 ? (
                  <Select
                    value=""
                    onChange={(e) => {
                      if (e.target.value) {
                        setEditFallbackModels([...editFallbackModels, e.target.value]);
                        e.target.value = '';
                      }
                    }}
                  >
                    <option value="">+ Add fallback model...</option>
                    {availableModels
                      .filter((m) => m.id !== editModel && !editFallbackModels.includes(m.id))
                      .map((m) => (
                        <option key={m.id} value={m.id}>{m.id} ({m.provider})</option>
                      ))}
                  </Select>
                ) : (
                  <p className="text-[11px] text-text-muted m-0">
                    Configure providers in Settings → AI Models to see available fallback models.
                  </p>
                )}
              </FormField>

              {/* Connectors / MCP Servers */}
              <FormField label="Connectors (MCP Servers)">
                <p className="text-[11px] text-text-muted mt-0 mb-2">
                  Select which connectors this agent can use. Each connector provides tools from its MCP server.
                </p>
                {editMcpServers.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {editMcpServers.map((s) => {
                      const c = connectors.find((cc) => cc.name === s);
                      return (
                        <span
                          key={s}
                          className="inline-flex items-center gap-1 text-[11px] bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium"
                        >
                          <Plug size={10} />
                          {c?.display_name || s}
                          <button
                            type="button"
                            onClick={() => setEditMcpServers(editMcpServers.filter((m) => m !== s))}
                            className="p-0 bg-transparent border-none cursor-pointer text-primary/60 hover:text-primary"
                          >
                            <X size={10} />
                          </button>
                        </span>
                      );
                    })}
                  </div>
                )}
                {connectors.length > 0 ? (
                  <Select
                    value=""
                    onChange={(e) => {
                      if (e.target.value && !editMcpServers.includes(e.target.value)) {
                        setEditMcpServers([...editMcpServers, e.target.value]);
                        e.target.value = '';
                      }
                    }}
                  >
                    <option value="">+ Add connector...</option>
                    {connectors
                      .filter((c) => !editMcpServers.includes(c.name))
                      .map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.display_name}{c.connected ? '' : ' (not connected)'}
                        </option>
                      ))}
                  </Select>
                ) : (
                  <p className="text-[11px] text-text-muted m-0">
                    No connectors available. Configure connectors in{' '}
                    <Link href="/settings/services" className="text-primary hover:underline">
                      Settings → Connectors
                    </Link>.
                  </p>
                )}
              </FormField>

              <div className="flex gap-sm">
                <Button onClick={handleSave} disabled={saving}>{saving ? 'Saving...' : 'Save Changes'}</Button>
                <Button variant="ghost" onClick={() => { setEditing(false); if (agent) populateEditFields(agent); }}>Cancel</Button>
              </div>
            </div>
          )}
        </Card>

        {/* Few-shot Examples */}
        <FewShotExamplesSection
          agentName={name}
          examples={examples}
          setExamples={setExamples}
          toast={toast}
        />
        </>
      )}

      {activeTab === 'memory' && (
        <AgentMemoryConfigPanel
          agentName={name}
          onSave={async (config) => {
            await adminApi.updateAgentConfig(name, config as unknown as Record<string, unknown>);
          }}
        />
      )}

      {activeTab === 'runs' && (
        <Card>
          <p className="text-sm text-text-secondary">
            This agent has {agent.total_runs} recorded run{agent.total_runs !== 1 ? 's' : ''}.{' '}
            <Link href={`/runs?agent=${encodeURIComponent(agent.name)}`} className="text-primary no-underline hover:underline">
              View in Run History
            </Link>
          </p>
        </Card>
      )}
    </div>
  );
}

/* ─── Few-shot Examples Section ─── */

function FewShotExamplesSection({
  agentName,
  examples,
  setExamples,
  toast,
}: {
  agentName: string;
  examples: PromptLogSummary[];
  setExamples: React.Dispatch<React.SetStateAction<PromptLogSummary[]>>;
  toast: ReturnType<typeof useToast>['toast'];
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [addingNew, setAddingNew] = useState(false);
  const [newInput, setNewInput] = useState('');
  const [newOutput, setNewOutput] = useState('');
  const [addingBusy, setAddingBusy] = useState(false);

  async function handleRemove(ex: PromptLogSummary) {
    try {
      await adminApi.updatePromptLog(ex.log_id, { is_example: false });
      setExamples((prev) => prev.filter((e) => e.log_id !== ex.log_id));
      toast('success', 'Removed from examples');
    } catch {
      toast('error', 'Failed to remove');
    }
  }

  async function handleAdd() {
    if (!newInput.trim() || !newOutput.trim()) return;
    setAddingBusy(true);
    try {
      await adminApi.savePrompt({
        agent_name: agentName,
        input_text: newInput.trim(),
        output_text: newOutput.trim(),
        source: 'playground',
        is_example: true,
        tags: ['manual'],
      });
      // Reload examples to get the full record
      const updated = await adminApi.listExamples(agentName);
      setExamples(updated);
      setNewInput('');
      setNewOutput('');
      setAddingNew(false);
      toast('success', 'Example added');
    } catch {
      toast('error', 'Failed to add example');
    } finally {
      setAddingBusy(false);
    }
  }

  return (
    <Card className="mt-md">
      <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase flex items-center gap-2">
        <Bookmark size={14} />
        Few-shot Examples ({examples.length})
      </h3>
      <div className="bg-primary/5 border border-primary/15 rounded-lg px-3 py-2.5 mb-md text-xs text-text-secondary">
        These examples are <strong>automatically injected</strong> as context when you chat with this agent in the{' '}
        <Link href="/playground" className="text-primary hover:underline">Playground</Link>.
        Each example is sent as a user/assistant message pair before your prompt, teaching the model the expected input→output pattern.
        You can also save examples from{' '}
        <Link href="/observability/prompts" className="text-primary hover:underline">Prompt History</Link>.
      </div>

      {examples.length === 0 && !addingNew ? (
        <div className="text-sm text-text-muted mb-md">
          No examples saved yet. Add one below or save prompts from{' '}
          <Link href="/observability/prompts" className="text-primary no-underline hover:underline">
            Prompt History
          </Link>.
        </div>
      ) : (
        <div className="flex flex-col gap-2 mb-md">
          {examples.map((ex) => {
            const isOpen = expandedId === ex.log_id;
            return (
              <div key={ex.log_id} className="bg-bg-subtle rounded-lg border border-border/50 overflow-hidden">
                {/* Collapsed header */}
                <button
                  type="button"
                  onClick={() => setExpandedId(isOpen ? null : ex.log_id)}
                  className="w-full flex items-start gap-2 px-3 py-2.5 text-left bg-transparent border-none cursor-pointer hover:bg-white/[0.03] transition-colors"
                >
                  {isOpen ? <ChevronDown size={12} className="mt-0.5 shrink-0 text-text-muted" /> : <ChevronRight size={12} className="mt-0.5 shrink-0 text-text-muted" />}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-1">
                      <Badge variant="success">example</Badge>
                      {ex.tags.map((tag) => (
                        <Badge key={tag} variant="info">{tag}</Badge>
                      ))}
                      <span className="text-[11px] text-text-muted">
                        {new Date(ex.created_at * 1000).toLocaleDateString()}
                      </span>
                    </div>
                    <div className="text-xs text-text-secondary truncate">
                      <span className="text-text-muted font-semibold">In: </span>
                      {ex.input_text.slice(0, 120)}{ex.input_text.length > 120 ? '...' : ''}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); handleRemove(ex); }}
                    className="text-text-muted hover:text-error border-none bg-transparent cursor-pointer p-1 shrink-0"
                    title="Remove from examples"
                  >
                    <Trash2 size={12} />
                  </button>
                </button>

                {/* Expanded content */}
                {isOpen && (
                  <div className="border-t border-border/50 px-3 py-3">
                    <div className="mb-3">
                      <div className="text-[11px] font-semibold text-primary uppercase mb-1">User Input</div>
                      <pre className="bg-bg-surface p-3 rounded-md text-xs whitespace-pre-wrap font-[family-name:var(--font-mono)] m-0 max-h-[200px] overflow-auto border border-border/30">
                        {ex.input_text}
                      </pre>
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold text-success uppercase mb-1">Expected Output</div>
                      <pre className="bg-bg-surface p-3 rounded-md text-xs whitespace-pre-wrap font-[family-name:var(--font-mono)] m-0 max-h-[300px] overflow-auto border border-success/20">
                        {ex.output_text}
                      </pre>
                    </div>
                    {ex.model && (
                      <div className="mt-2 text-[11px] text-text-muted">
                        Model: <span className="font-[family-name:var(--font-mono)]">{ex.model}</span>
                        {(ex.input_tokens + ex.output_tokens) > 0 && <> · {(ex.input_tokens + ex.output_tokens).toLocaleString()} tokens</>}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Add new example inline */}
      {addingNew ? (
        <div className="p-3 bg-bg-subtle rounded-lg border border-border">
          <div className="text-xs font-semibold text-text-muted uppercase mb-2">Add Example</div>
          <div className="mb-3">
            <label className="block text-[11px] text-text-muted mb-1">User Input (what the user sends)</label>
            <textarea
              value={newInput}
              onChange={(e) => setNewInput(e.target.value)}
              placeholder="e.g. Summarize the quarterly earnings report for Q3 2025."
              className="w-full px-3 py-2 rounded-md border border-border text-xs bg-bg-surface font-[family-name:var(--font-mono)] min-h-[80px] outline-none focus:border-primary resize-y"
            />
          </div>
          <div className="mb-3">
            <label className="block text-[11px] text-text-muted mb-1">Expected Output (how the agent should respond)</label>
            <textarea
              value={newOutput}
              onChange={(e) => setNewOutput(e.target.value)}
              placeholder="e.g. Q3 2025 saw a 12% revenue increase driven by..."
              className="w-full px-3 py-2 rounded-md border border-border text-xs bg-bg-surface font-[family-name:var(--font-mono)] min-h-[100px] outline-none focus:border-primary resize-y"
            />
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleAdd} disabled={addingBusy || !newInput.trim() || !newOutput.trim()}>
              {addingBusy ? 'Adding...' : 'Add Example'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => { setAddingNew(false); setNewInput(''); setNewOutput(''); }}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" size="sm" onClick={() => setAddingNew(true)}>
          <Plus size={12} className="mr-1" />
          Add Example
        </Button>
      )}
    </Card>
  );
}
