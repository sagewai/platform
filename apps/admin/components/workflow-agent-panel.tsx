'use client';

import { useState } from 'react';
import { Button, Badge, TextInput, TextArea } from '@sagecurator/ui';
import type { AgentNodeDef } from '@/utils/workflow-types';
import type { AgentSummary, AvailableModel } from '@/utils/types';

interface Props {
  agents: Record<string, AgentNodeDef>;
  registeredAgents: AgentSummary[];
  availableModels: AvailableModel[];
  onChange: (agents: Record<string, AgentNodeDef>) => void;
}

export function WorkflowAgentPanel({ agents, registeredAgents, availableModels, onChange }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [newInlineName, setNewInlineName] = useState('');

  function addFromRegistry(ra: AgentSummary) {
    const key = ra.name.replace(/[^a-zA-Z0-9_-]/g, '-');
    if (agents[key]) return; // already added
    onChange({ ...agents, [key]: { ref: ra.name } });
  }

  function addInline() {
    const key = newInlineName.trim().replace(/[^a-zA-Z0-9_-]/g, '-');
    if (!key || agents[key]) return;
    const firstModel = availableModels.length > 0 ? availableModels[0] : null;
    onChange({ ...agents, [key]: {
      model: firstModel?.id ?? 'gpt-4o',
      system_prompt: '',
      api_base: firstModel?.api_base || undefined,
    } });
    setNewInlineName('');
    setExpanded(key);
  }

  function updateAgent(name: string, def: AgentNodeDef) {
    onChange({ ...agents, [name]: def });
  }

  function removeAgent(name: string) {
    const next = { ...agents };
    delete next[name];
    onChange(next);
    if (expanded === name) setExpanded(null);
  }

  const entries = Object.entries(agents);
  const availableToAdd = registeredAgents.filter(
    (ra) => !Object.values(agents).some((d) => d.ref === ra.name),
  );

  return (
    <div className="flex flex-col gap-2">
      {/* Agent list */}
      {entries.length === 0 && (
        <p className="text-[12px] text-text-muted m-0 py-2">
          No agents added yet. Pick from your registered agents below.
        </p>
      )}
      {entries.map(([name, def]) => {
        const isRef = !!def.ref;
        const isOpen = expanded === name;
        // Look up registry info for ref agents
        const registryInfo = isRef
          ? registeredAgents.find((ra) => ra.name === def.ref)
          : null;

        return (
          <div
            key={name}
            className="border border-border rounded-lg bg-bg-surface overflow-hidden"
          >
            {/* Row header */}
            <div className="flex items-center gap-2 px-3 py-2 hover:bg-bg-subtle transition-colors">
              <button
                type="button"
                onClick={() => setExpanded(isOpen ? null : name)}
                className="flex items-center gap-2 flex-1 min-w-0 text-left border-none bg-transparent cursor-pointer p-0"
              >
                <span className="text-xs mr-0.5">{isOpen ? '▾' : '▸'}</span>
                <span className="text-sm font-medium text-text-primary truncate flex-1">{name}</span>
                <Badge variant={isRef ? 'info' : 'default'} className="text-[10px]">
                  {isRef ? 'registered' : 'inline'}
                </Badge>
                {registryInfo && (
                  <span className="text-[10px] text-text-muted font-[family-name:var(--font-mono)]">
                    {registryInfo.model}
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); removeAgent(name); }}
                className="text-[11px] text-text-muted hover:text-error border-none bg-transparent cursor-pointer shrink-0 px-1"
                title={`Remove ${name}`}
              >
                ✕
              </button>
            </div>

            {/* Expanded config */}
            {isOpen && (
              <div className="px-3 pb-3 pt-1 border-t border-border flex flex-col gap-2">
                {isRef ? (
                  <div className="text-[11px] text-text-muted">
                    <p className="m-0 mb-1">
                      Uses registered agent <strong>{def.ref}</strong>
                    </p>
                    {registryInfo && (
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                        <span>Model: <strong className="text-text-primary">{registryInfo.model}</strong></span>
                        <span>Strategy: <strong className="text-text-primary">{registryInfo.strategy || 'default'}</strong></span>
                        <span>Status: <strong className="text-text-primary">{registryInfo.status}</strong></span>
                      </div>
                    )}
                  </div>
                ) : (
                  <>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-text-muted w-20 shrink-0">Model</span>
                      <select
                        value={def.model ?? ''}
                        onChange={(e) => {
                          const selectedModel = availableModels.find((m) => m.id === e.target.value);
                          updateAgent(name, {
                            ...def,
                            model: e.target.value,
                            api_base: selectedModel?.api_base || undefined,
                          });
                        }}
                        className="flex-1 px-2 py-1 rounded border border-border text-xs bg-bg-surface"
                      >
                        {availableModels.length === 0 && (
                          <option value={def.model ?? ''}>{def.model || 'No models available'}</option>
                        )}
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
                    </div>
                    <div className="flex items-start gap-2">
                      <span className="text-[11px] text-text-muted w-20 shrink-0 pt-2">Prompt</span>
                      <TextArea
                        value={def.system_prompt ?? ''}
                        onChange={(e) => updateAgent(name, { ...def, system_prompt: e.target.value })}
                        placeholder="System prompt..."
                        className="font-[family-name:var(--font-mono)] text-xs min-h-[60px]"
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-text-muted w-20 shrink-0">Temperature</span>
                      <input
                        type="number"
                        min={0}
                        max={2}
                        step={0.1}
                        value={def.temperature ?? 0.7}
                        onChange={(e) =>
                          updateAgent(name, { ...def, temperature: parseFloat(e.target.value) || 0.7 })
                        }
                        className="w-20 px-2 py-1 rounded border border-border text-xs bg-bg-surface"
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-text-muted w-20 shrink-0">Max iters</span>
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={def.max_iterations ?? 10}
                        onChange={(e) =>
                          updateAgent(name, {
                            ...def,
                            max_iterations: parseInt(e.target.value, 10) || 10,
                          })
                        }
                        className="w-20 px-2 py-1 rounded border border-border text-xs bg-bg-surface"
                      />
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Add agents */}
      <div className="flex flex-col gap-2 mt-1">
        {/* Registry agents as clickable list */}
        {availableToAdd.length > 0 && (
          <div className="border border-border rounded-lg overflow-hidden">
            <div className="px-3 py-1.5 text-[11px] text-text-muted bg-bg-subtle border-b border-border uppercase tracking-wide">
              Available Agents ({availableToAdd.length})
            </div>
            <div className="max-h-[200px] overflow-auto">
              {availableToAdd.map((ra) => (
                <button
                  key={ra.name}
                  type="button"
                  onClick={() => addFromRegistry(ra)}
                  className="w-full text-left px-3 py-2 text-[13px] hover:bg-bg-subtle border-none bg-transparent cursor-pointer transition-colors flex items-center justify-between gap-2 border-b border-border/50 last:border-b-0"
                >
                  <span className="font-medium text-text-primary truncate">{ra.name}</span>
                  <span className="text-[10px] text-text-muted font-[family-name:var(--font-mono)] shrink-0">
                    {ra.model} · {ra.strategy || 'default'}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {availableToAdd.length === 0 && registeredAgents.length > 0 && entries.length > 0 && (
          <p className="text-[11px] text-text-muted m-0">All registered agents have been added.</p>
        )}

        {registeredAgents.length === 0 && (
          <p className="text-[11px] text-text-muted m-0">
            No registered agents found. Create agents in the Agents page first.
          </p>
        )}

        {/* Inline agent creation */}
        <details className="group">
          <summary className="text-[11px] text-text-muted cursor-pointer select-none hover:text-text-primary list-none flex items-center gap-1">
            <span className="text-[10px] group-open:rotate-90 transition-transform">▶</span>
            Create inline agent (advanced)
          </summary>
          <div className="flex items-center gap-1 mt-1.5">
            <TextInput
              value={newInlineName}
              onChange={(e) => setNewInlineName(e.target.value)}
              placeholder="agent-name"
              className="text-xs w-32"
              onKeyDown={(e) => e.key === 'Enter' && addInline()}
            />
            <Button
              variant="secondary"
              className="text-xs"
              onClick={addInline}
              disabled={!newInlineName.trim()}
            >
              + Add
            </Button>
          </div>
        </details>
      </div>
    </div>
  );
}
