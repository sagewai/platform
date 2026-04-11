'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { playgroundApi } from '@/utils/playground-api';
import { adminApi } from '@/utils/api';
import type { AgentSpec, InferencePreset } from '@/utils/playground-api';
import type { AvailableModel, CapabilityItem } from '@/utils/types';
import { Card, Button } from '@/components/ui/legacy';
import { AlertCircle, ChevronDown, ChevronRight, Brain, Sparkles, HelpCircle, Copy, Check } from 'lucide-react';

export interface AgentConfigDefaults {
  name?: string;
  model?: string;
  system_prompt?: string;
  temperature?: number;
  strategy?: string;
  tools?: string[];
  mcp_servers?: string[];
  memory_backends?: string[];
  guardrails?: string[];
}

interface Props {
  onAgentCreated: (name: string) => void;
  defaults?: AgentConfigDefaults;
}

interface CapabilityCatalog {
  tools: CapabilityItem[];
  mcp_servers: CapabilityItem[];
  memory: CapabilityItem[];
  guardrails: CapabilityItem[];
  strategies: CapabilityItem[];
}

function CapabilitySection({
  title,
  items,
  selected,
  onToggle,
}: {
  title: string;
  items: CapabilityItem[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  const [open, setOpen] = useState(selected.length > 0);

  return (
    <div className="border border-border rounded-md">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 bg-transparent border-none text-[13px] font-semibold text-text-secondary cursor-pointer hover:bg-bg-subtle transition-colors rounded-md"
      >
        <span>
          {title}
          {selected.length > 0 && (
            <span className="ml-2 text-[11px] font-normal text-primary">
              {selected.length} selected
            </span>
          )}
        </span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <div className="px-3 pb-3 flex flex-col gap-1.5">
          {items.map((item) => {
            const isSelected = selected.includes(item.id);
            return (
              <label
                key={item.id}
                className={`flex items-start gap-2.5 p-2 rounded-md cursor-pointer transition-colors ${
                  isSelected ? 'bg-primary/10 border border-primary/30' : 'bg-bg-subtle border border-transparent hover:border-border'
                }`}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => onToggle(item.id)}
                  className="mt-0.5 accent-primary"
                />
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium text-text-primary">{item.name}</div>
                  <div className="text-[11px] text-text-muted mt-0.5">{item.description}</div>
                </div>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function AgentConfigPanel({ onAgentCreated, defaults }: Props) {
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [strategies, setStrategies] = useState<string[]>([]);
  const [presets, setPresets] = useState<InferencePreset[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilityCatalog | null>(null);
  const [name, setName] = useState(defaults?.name ?? 'test-agent');
  const [model, setModel] = useState(defaults?.model ?? '');
  const [systemPrompt, setSystemPrompt] = useState(defaults?.system_prompt ?? 'You are a helpful assistant.');
  const [strategy, setStrategy] = useState(defaults?.strategy ?? 'react');
  const [preset, setPreset] = useState<string | null>(null);
  const [temperature, setTemperature] = useState(defaults?.temperature ?? 0.7);
  const [topP, setTopP] = useState<number | null>(null);
  const [maxTokens, setMaxTokens] = useState<number | null>(null);
  const [maxIterations, setMaxIterations] = useState(10);
  const [frequencyPenalty, setFrequencyPenalty] = useState<number | null>(null);
  const [presencePenalty, setPresencePenalty] = useState<number | null>(null);
  const [apiBase, setApiBase] = useState<string | null>(null);
  const [autoLearn, setAutoLearn] = useState(false);
  const [showDirectiveHelp, setShowDirectiveHelp] = useState(false);
  const [copiedExample, setCopiedExample] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  // Capability selections
  const [selectedTools, setSelectedTools] = useState<string[]>(defaults?.tools ?? []);
  const [selectedMcp, setSelectedMcp] = useState<string[]>(defaults?.mcp_servers ?? []);
  const [selectedMemory, setSelectedMemory] = useState<string[]>(defaults?.memory_backends ?? []);
  const [selectedGuardrails, setSelectedGuardrails] = useState<string[]>(defaults?.guardrails ?? []);

  useEffect(() => {
    adminApi.listAvailableModels().then((models) => {
      setAvailableModels(models);
      // If current model (from template or default) isn't in the available list, fall back
      const currentValid = model && models.some((m) => m.id === model);
      if (!currentValid && models.length > 0) {
        // Prefer a tool-capable model as the fallback
        const fallback = models.find((m) => m.supports_tools) ?? models[0];
        setModel(fallback.id);
        setApiBase(fallback.api_base ?? null);
      } else if (currentValid) {
        const matched = models.find((m) => m.id === model);
        if (matched?.api_base) setApiBase(matched.api_base);
      }
    }).catch(() => {});
    playgroundApi.listStrategies().then(setStrategies).catch(() => {});
    playgroundApi.listPresets().then(setPresets).catch(() => {});
    playgroundApi.listCapabilities().then((caps) => setCapabilities(caps as unknown as CapabilityCatalog)).catch(() => {});
  }, []);

  function toggleItem(list: string[], setList: (v: string[]) => void, id: string) {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  function handlePresetChange(value: string) {
    if (value === '') {
      setPreset(null);
      return;
    }
    setPreset(value);
    const p = presets.find((pr) => pr.name === value);
    if (p) {
      setTemperature(p.temperature);
      setTopP(p.top_p);
    }
  }

  async function handleCreate() {
    setCreating(true);
    setError('');
    try {
      const spec: AgentSpec = {
        name,
        model,
        system_prompt: systemPrompt,
        strategy,
        temperature,
        preset,
        top_p: topP,
        max_tokens: maxTokens,
        max_iterations: maxIterations,
        frequency_penalty: frequencyPenalty,
        presence_penalty: presencePenalty,
        tools: selectedTools,
        mcp_servers: selectedMcp,
        memory_backends: selectedMemory,
        guardrails: selectedGuardrails,
        api_base: apiBase,
        auto_learn: autoLearn,
      };
      await playgroundApi.createAgent(spec);
      onAgentCreated(name);
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <Card className="flex flex-col gap-3">
      <h3 className="m-0 text-[14px] font-semibold">
        Agent Configuration
      </h3>

      <label className="text-[13px] text-text-secondary">
        Name
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface box-border"
        />
      </label>

      <label className="text-[13px] text-text-secondary">
        Model
        {availableModels.length > 0 ? (
          <select value={model} onChange={(e) => {
            setModel(e.target.value);
            const selected = availableModels.find((m) => m.id === e.target.value);
            setApiBase(selected?.api_base ?? null);
          }} className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface">
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
          </select>
        ) : (
          <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2.5 mt-1">
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
      </label>

      <label className="text-[13px] text-text-secondary">
        Strategy
        <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface">
          {capabilities?.strategies ? (
            capabilities.strategies.map((s) => (
              <option key={s.id} value={s.id} title={s.description}>
                {s.name}
              </option>
            ))
          ) : (
            strategies.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))
          )}
        </select>
      </label>

      <label className="text-[13px] text-text-secondary">
        System Prompt
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={2}
          className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface font-[inherit] resize-y box-border"
        />
      </label>
      <button
        type="button"
        onClick={() => setShowDirectiveHelp(!showDirectiveHelp)}
        className="flex items-center gap-1.5 bg-transparent border-none p-0 text-[12px] text-primary cursor-pointer text-left font-medium"
      >
        <Sparkles size={12} />
        {showDirectiveHelp ? 'Hide' : 'Show'} directive &amp; context syntax
        <HelpCircle size={11} className="text-text-muted" />
      </button>

      {showDirectiveHelp && (
        <div className="rounded-lg border border-primary/20 bg-primary/5 overflow-hidden">
          <div className="px-3 py-2.5 border-b border-primary/10">
            <div className="text-[12px] font-semibold text-text-secondary mb-1">
              Use directives in your system prompt to connect agents to knowledge
            </div>
            <div className="text-[11px] text-text-muted">
              Directives are resolved <span className="font-medium">before</span> the prompt reaches the LLM — they inject real data from your knowledge base.
            </div>
          </div>

          <div className="px-3 py-2 space-y-2">
            {[
              {
                label: 'Retrieve knowledge',
                sigil: "@context('product pricing and plans')",
                desc: 'Searches all uploaded documents and injects the most relevant chunks',
              },
              {
                label: 'Recall memories',
                sigil: "@memory('previous customer interactions')",
                desc: 'Retrieves from the agent\u2019s personal memory store',
              },
              {
                label: 'Delegate to another agent',
                sigil: "@agent:researcher('find latest market data')",
                desc: 'Asks another registered agent for help inline',
              },
              {
                label: 'Override model',
                sigil: '#model:claude-sonnet-4-20250514',
                desc: 'Switch the LLM for this turn only',
              },
              {
                label: 'Set budget',
                sigil: '#budget:0.50',
                desc: 'Limit cost for this turn (in USD)',
              },
            ].map((item) => (
              <div key={item.sigil} className="flex items-start gap-2 group">
                <div className="flex-1 min-w-0">
                  <div className="text-[11px] font-medium text-text-secondary">{item.label}</div>
                  <code className="text-[11px] text-primary block mt-0.5 break-all">{item.sigil}</code>
                  <div className="text-[10px] text-text-muted mt-0.5">{item.desc}</div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard.writeText(item.sigil);
                    setCopiedExample(item.sigil);
                    setTimeout(() => setCopiedExample(null), 1500);
                  }}
                  className="bg-transparent border-none p-1 text-text-muted hover:text-primary cursor-pointer opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                  title="Copy to clipboard"
                >
                  {copiedExample === item.sigil ? <Check size={11} className="text-green-400" /> : <Copy size={11} />}
                </button>
              </div>
            ))}
          </div>

          <div className="px-3 py-2 border-t border-primary/10 bg-primary/5">
            <div className="text-[11px] font-semibold text-text-secondary mb-1.5">Example system prompt:</div>
            <pre className="text-[11px] text-text-muted leading-relaxed m-0 whitespace-pre-wrap font-[family-name:var(--font-mono)]">{`You are a customer support agent.

Use this knowledge when answering:
@context('product features and pricing')
@context('refund and cancellation policy')

Remember our previous conversations:
@memory('past interactions with this customer')`}</pre>
          </div>

          <div className="px-3 py-1.5 border-t border-primary/10">
            <a href="/context/directives" className="text-[11px] text-primary hover:underline font-medium">
              Full directive reference →
            </a>
          </div>
        </div>
      )}

      {/* ─── Agentic Capabilities ─── */}
      {capabilities && (
        <div className="flex flex-col gap-1.5">
          <h4 className="m-0 text-[12px] font-semibold text-text-secondary uppercase tracking-wide">
            Capabilities
          </h4>
          {availableModels.find((m) => m.id === model)?.supports_tools === false && selectedTools.length > 0 && (
            <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-[12px]">
              <AlertCircle size={14} className="text-warning shrink-0 mt-0.5" />
              <div>
                <span className="text-text-secondary font-medium">{model}</span>
                <span className="text-text-muted"> does not support tool calling. Tools will be ignored by this model.</span>
              </div>
            </div>
          )}
          <CapabilitySection
            title={`Tools${availableModels.find((m) => m.id === model)?.supports_tools === false ? ' (not supported by model)' : ''}`}
            items={capabilities.tools}
            selected={selectedTools}
            onToggle={(id) => toggleItem(selectedTools, setSelectedTools, id)}
          />
          <CapabilitySection
            title="MCP Servers"
            items={capabilities.mcp_servers}
            selected={selectedMcp}
            onToggle={(id) => toggleItem(selectedMcp, setSelectedMcp, id)}
          />
          <CapabilitySection
            title="Memory Backends"
            items={capabilities.memory}
            selected={selectedMemory}
            onToggle={(id) => toggleItem(selectedMemory, setSelectedMemory, id)}
          />
          <CapabilitySection
            title="Guardrails"
            items={capabilities.guardrails}
            selected={selectedGuardrails}
            onToggle={(id) => toggleItem(selectedGuardrails, setSelectedGuardrails, id)}
          />
        </div>
      )}

      {/* Context & Memory */}
      <div className="flex items-center justify-between p-3 rounded-md border border-border">
        <div className="flex items-center gap-2">
          <Brain size={14} className="text-teal-400" />
          <div>
            <div className="text-[13px] font-medium text-text-primary">Auto-Learn</div>
            <div className="text-[11px] text-text-muted">Extract and store memories from conversations</div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setAutoLearn(!autoLearn)}
          className={`relative w-9 h-5 rounded-full transition-colors border-none cursor-pointer ${
            autoLearn ? 'bg-primary' : 'bg-white/20'
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
              autoLearn ? 'translate-x-4' : ''
            }`}
          />
        </button>
      </div>

      {/* Inference Parameters (collapsible) */}
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="bg-transparent border-none p-0 text-[12px] text-primary cursor-pointer text-left font-medium"
      >
        {showAdvanced ? '- Hide' : '+ Show'} Inference Parameters
        <span className="ml-1.5 text-text-muted font-normal">
          temp={temperature.toFixed(1)}
        </span>
      </button>

      {showAdvanced && (
        <div className="flex flex-col gap-3">
          <label className="text-[13px] text-text-secondary">
            Inference Preset
            <select
              value={preset ?? ''}
              onChange={(e) => handlePresetChange(e.target.value)}
              className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface"
            >
              <option value="">Custom</option>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name} (temp={p.temperature}, top_p={p.top_p})
                </option>
              ))}
            </select>
          </label>

          <label className="text-[13px] text-text-secondary">
            Temperature: {temperature.toFixed(1)}
            <input
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(e) => {
                setTemperature(parseFloat(e.target.value));
                setPreset(null);
              }}
              className="block w-full mt-1"
            />
          </label>
          <label className="text-[13px] text-text-secondary">
            Top P {topP != null ? `: ${topP}` : ' (default)'}
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={topP ?? 0.95}
              onChange={(e) => {
                setTopP(parseFloat(e.target.value));
                setPreset(null);
              }}
              className="block w-full mt-1"
            />
          </label>

          <label className="text-[13px] text-text-secondary">
            Max Tokens
            <input
              type="number"
              min={1}
              max={128000}
              placeholder="Default (model limit)"
              value={maxTokens ?? ''}
              onChange={(e) => setMaxTokens(e.target.value ? parseInt(e.target.value) : null)}
              className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface box-border"
            />
          </label>

          <label className="text-[13px] text-text-secondary">
            Max Iterations
            <input
              type="number"
              min={1}
              max={50}
              value={maxIterations}
              onChange={(e) => setMaxIterations(parseInt(e.target.value) || 10)}
              className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface box-border"
            />
          </label>

          <label className="text-[13px] text-text-secondary">
            Frequency Penalty {frequencyPenalty != null ? `: ${frequencyPenalty}` : ' (default: 0)'}
            <input
              type="range"
              min={-2}
              max={2}
              step={0.1}
              value={frequencyPenalty ?? 0}
              onChange={(e) => setFrequencyPenalty(parseFloat(e.target.value))}
              className="block w-full mt-1"
            />
          </label>

          <label className="text-[13px] text-text-secondary">
            Presence Penalty {presencePenalty != null ? `: ${presencePenalty}` : ' (default: 0)'}
            <input
              type="range"
              min={-2}
              max={2}
              step={0.1}
              value={presencePenalty ?? 0}
              onChange={(e) => setPresencePenalty(parseFloat(e.target.value))}
              className="block w-full mt-1"
            />
          </label>
        </div>
      )}

      {error && <div className="text-error text-[13px]">{error}</div>}

      <Button onClick={handleCreate} disabled={creating || !name.trim() || !model}>
        {creating ? 'Creating...' : 'Create Agent'}
      </Button>
    </Card>
  );
}
