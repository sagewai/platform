'use client';

import { useState } from 'react';
import { Card, Button, useToast } from '@/components/ui/legacy';
import { Sparkles, BookOpen, Cpu, Brain } from 'lucide-react';
import { adminApi } from '@/utils/api';
import { ScopeBadge } from '@/components/scope-badge';
import { ScopeSelector } from '@/components/scope-selector';
import type { ContextDocument } from '@/utils/types';

const SIGILS = [
  { sigil: '@context(\'query\', scope=\'...\', tags=\'...\')', description: 'Retrieve relevant context from the Context Engine. Optional scope (org/project) and comma-separated tags for filtering.', example: '@context(\'customer billing history\', scope=\'org\', tags=\'finance,billing\')' },
  { sigil: '@memory(\'query\')', description: 'Retrieve from agent\'s personal memory store', example: '@memory(\'previous conversation topics\')' },
  { sigil: '@agent:name(\'task\')', description: 'Delegate a subtask to another agent', example: '@agent:researcher(\'find latest pricing\')' },
  { sigil: '@wf:name(\'input\')', description: 'Invoke a saved workflow from the registry', example: '@wf:research-pipeline(\'latest AI trends\')' },
  { sigil: '/tool.name(\'args\')', description: 'Invoke a registered tool by name', example: '/tool.web_search(\'AI market trends 2026\')' },
  { sigil: '/mcp.server.tool(\'args\')', description: 'Invoke a tool via MCP connector', example: '/mcp.slack.send_message(\'#general\', \'Hello!\')' },
  { sigil: '#model:model_name', description: 'Override the LLM model for this turn', example: '#model:claude-sonnet-4-20250514' },
  { sigil: '#budget:amount', description: 'Set a cost budget limit for this turn', example: '#budget:0.50' },
];

const TEMPLATES = [
  { syntax: '{{ context.search(\'query\') }}', description: 'Inline context retrieval in prompt templates' },
  { syntax: '{{ memory.retrieve(\'query\') }}', description: 'Inline memory retrieval' },
  { syntax: '{{ agent.delegate(\'name\', \'task\') }}', description: 'Inline agent delegation' },
  { syntax: '{{ tool.call(\'name\', args) }}', description: 'Inline tool invocation' },
];

const DYNAMIC_PARAMS = [
  { variable: '@datetime', description: 'Current UTC timestamp in ISO 8601 format', example: '2026-03-28T14:30:00+00:00' },
  { variable: '@date', description: 'Current UTC date', example: '2026-03-28' },
  { variable: '@time', description: 'Current UTC time', example: '14:30:00' },
  { variable: '@user', description: 'Current user ID', example: 'user_abc123' },
  { variable: '@project', description: 'Current project ID', example: 'default' },
];

const MODEL_PROFILES = [
  {
    name: 'Small',
    description: 'For local/small models (< 7B params). Maximum compression, prompt-based tool calling, minimal context.',
    settings: { compression: 'High (3:1)', context_budget: '1024 tokens', tool_mode: 'Prompt-based', examples: 'Minimal' },
    color: 'text-green-400',
    bgColor: 'bg-green-500/10 border-green-500/20',
  },
  {
    name: 'Medium',
    description: 'For mid-range models (7B-70B). Moderate compression, balanced context window usage.',
    settings: { compression: 'Medium (2:1)', context_budget: '4096 tokens', tool_mode: 'Native (if supported)', examples: 'Balanced' },
    color: 'text-blue-400',
    bgColor: 'bg-blue-500/10 border-blue-500/20',
  },
  {
    name: 'Large',
    description: 'For frontier models (GPT-4o, Claude, Gemini). Minimal compression, full context, native tool calling.',
    settings: { compression: 'Low (1.5:1)', context_budget: '16384 tokens', tool_mode: 'Native', examples: 'Full' },
    color: 'text-purple-400',
    bgColor: 'bg-purple-500/10 border-purple-500/20',
  },
];

export default function DirectivesPage() {
  const [memoryScope, setMemoryScope] = useState('org');
  const [memoryScopeId, setMemoryScopeId] = useState('');
  const [memories, setMemories] = useState<ContextDocument[]>([]);
  const [loadingMemories, setLoadingMemories] = useState(false);
  const { toast } = useToast();

  async function browseMemories() {
    if (!memoryScopeId.trim() && memoryScope !== 'org') return;
    setLoadingMemories(true);
    try {
      const data = await adminApi.listContextMemories(memoryScope, memoryScopeId);
      setMemories(data.memories);
    } catch (e) {
      toast('error', `Failed: ${e instanceof Error ? e.message : 'unknown'}`);
      setMemories([]);
    } finally {
      setLoadingMemories(false);
    }
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-xl">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1">Directives</h1>
        <p className="text-text-muted text-sm max-w-[42rem]">
          The Directive Engine preprocesses prompts before they reach the LLM — resolving context lookups,
          tool calls, agent delegations, and model overrides. Works with any model, from small local models to frontier APIs.
        </p>
      </div>

      {/* Sigil Reference */}
      <Card className="p-lg mb-lg">
        <div className="flex items-center gap-2 mb-md">
          <Sparkles size={16} className="text-purple-400" />
          <h2 className="text-sm font-semibold">Sigil Reference</h2>
        </div>
        <p className="text-xs text-text-muted mb-md">
          Use sigils in system prompts and directive templates. They are resolved before the LLM sees the prompt.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-text-muted text-xs border-b border-border">
                <th className="pb-2 pr-4 font-medium">Sigil</th>
                <th className="pb-2 pr-4 font-medium">Description</th>
                <th className="pb-2 font-medium">Example</th>
              </tr>
            </thead>
            <tbody>
              {SIGILS.map((s) => (
                <tr key={s.sigil} className="border-b border-border">
                  <td className="py-2.5 pr-4">
                    <code className="bg-bg-subtle px-1.5 py-0.5 rounded text-xs font-mono text-primary">{s.sigil}</code>
                  </td>
                  <td className="py-2.5 pr-4 text-xs text-text-muted">{s.description}</td>
                  <td className="py-2.5">
                    <code className="text-xs font-mono text-text-muted">{s.example}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Template Syntax */}
      <Card className="p-lg mb-lg">
        <div className="flex items-center gap-2 mb-md">
          <BookOpen size={16} className="text-teal-400" />
          <h2 className="text-sm font-semibold">Template Syntax</h2>
        </div>
        <p className="text-xs text-text-muted mb-md">
          Use double-brace templates for inline resolution within prompt text.
        </p>
        <div className="space-y-2">
          {TEMPLATES.map((t) => (
            <div key={t.syntax} className="flex items-start gap-3 py-2 border-b border-border last:border-0">
              <code className="bg-bg-subtle px-2 py-1 rounded text-xs font-mono text-primary shrink-0">{t.syntax}</code>
              <span className="text-xs text-text-muted">{t.description}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Dynamic Parameters */}
      <Card className="p-lg mb-lg">
        <div className="flex items-center gap-2 mb-md">
          <Cpu size={16} className="text-cyan-400" />
          <h2 className="text-sm font-semibold">Dynamic Parameters</h2>
        </div>
        <p className="text-xs text-text-muted mb-md">
          Use backtick-delimited arguments with <code>@variable</code> references. Parameters are resolved before directive processing.
        </p>
        <div className="mb-md p-3 rounded-md bg-bg-deep border border-border">
          <code className="text-xs text-cyan-300">
            @agent:data-extraction(`What is the age of Alice Munro on @datetime`)
          </code>
        </div>
        <div className="space-y-2">
          {DYNAMIC_PARAMS.map((p) => (
            <div key={p.variable} className="flex items-start gap-3 text-xs">
              <code className="text-cyan-400 font-mono shrink-0 w-24">{p.variable}</code>
              <span className="text-text-muted">{p.description}</span>
              <code className="text-text-muted ml-auto shrink-0">{p.example}</code>
            </div>
          ))}
        </div>
      </Card>

      {/* Model Profiles */}
      <Card className="p-lg mb-lg">
        <div className="flex items-center gap-2 mb-md">
          <Cpu size={16} className="text-amber-400" />
          <h2 className="text-sm font-semibold">Model Profiles</h2>
        </div>
        <p className="text-xs text-text-muted mb-md">
          The Directive Engine auto-detects the model size and adjusts compression, context budgets, and tool calling mode accordingly.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-md">
          {MODEL_PROFILES.map((profile) => (
            <div key={profile.name} className={`border rounded-lg p-md ${profile.bgColor}`}>
              <h3 className={`text-sm font-semibold mb-1 ${profile.color}`}>{profile.name}</h3>
              <p className="text-[11px] text-text-muted mb-3">{profile.description}</p>
              <div className="space-y-1.5">
                {Object.entries(profile.settings).map(([key, val]) => (
                  <div key={key} className="flex justify-between text-[11px]">
                    <span className="text-text-muted capitalize">{key.replace('_', ' ')}</span>
                    <span className="font-medium font-mono">{val}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Memory Browser */}
      <Card className="p-lg">
        <div className="flex items-center gap-2 mb-md">
          <Brain size={16} className="text-rose-400" />
          <h2 className="text-sm font-semibold">Auto-Extracted Memories</h2>
        </div>
        <p className="text-xs text-text-muted mb-md">
          Browse memories auto-extracted from agent conversations. These are stored as context documents with source=conversation.
        </p>
        <div className="flex items-end gap-3 mb-md">
          <div className="flex-1">
            <ScopeSelector
              scope={memoryScope}
              scopeId={memoryScopeId}
              onChange={(s, id) => { setMemoryScope(s); setMemoryScopeId(id); }}
              layout="row"
            />
          </div>
          <Button size="sm" onClick={browseMemories} disabled={loadingMemories}>
            {loadingMemories ? 'Loading...' : 'Browse'}
          </Button>
        </div>

        {memories.length > 0 ? (
          <div className="space-y-2">
            {memories.map((m) => (
              <div key={m.id} className="border border-border rounded p-3 bg-bg-surface">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium">{m.title}</span>
                  <ScopeBadge scope={m.scope} />
                </div>
                <div className="text-[11px] text-text-muted">
                  {m.chunk_count} chunks &middot; {m.status} &middot; {m.created_at ? new Date(m.created_at).toLocaleDateString() : '—'}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-text-muted text-center py-md">
            Select a scope and ID, then click Browse to view auto-extracted memories.
          </div>
        )}
      </Card>
    </div>
  );
}
