'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { AgentConfigPanel } from './agent-config-panel';
import type { AgentConfigDefaults } from './agent-config-panel';
import { SSEChat } from './sse-chat';
import { adminApi } from '@/utils/api';
import { playgroundApi } from '@/utils/playground-api';
import type { AgentSpec } from '@/utils/playground-api';
import type { AvailableModel } from '@/utils/types';
import { ChevronLeft, Settings2, Copy, X, Check, Code2 } from 'lucide-react';
import { Button } from '@/components/ui/legacy';

/** Generate Python code from an AgentSpec. */
function generatePythonCode(spec: AgentSpec): string {
  const lines: string[] = [];

  // Imports
  lines.push('from sagewai.engines.universal import UniversalAgent');

  // Strategy import
  const strategyMap: Record<string, string> = {
    react: 'from sagewai.core.strategies import ReActStrategy',
    lats: 'from sagewai.core.lats import LATSStrategy',
    tree_of_thoughts: 'from sagewai.core.tree_of_thoughts import TreeOfThoughtsStrategy',
    self_correction: 'from sagewai.core.self_correction import SelfCorrectionStrategy',
  };
  if (spec.strategy && strategyMap[spec.strategy]) {
    lines.push(strategyMap[spec.strategy]);
  }

  // Tools
  if (spec.tools && spec.tools.length > 0) {
    lines.push('from sagewai.models.tool import ToolSpec');
  }

  // MCP
  if (spec.mcp_servers && spec.mcp_servers.length > 0) {
    lines.push('from sagewai.mcp.client import McpClient');
  }

  lines.push('');
  lines.push('');

  // Tool definitions
  if (spec.tools && spec.tools.length > 0) {
    lines.push('# Define your tool handlers');
    for (const tool of spec.tools) {
      lines.push(`def ${tool}_handler(**kwargs):`);
      lines.push(`    """Implement ${tool} logic here."""`);
      lines.push('    pass');
      lines.push('');
    }
    lines.push('tools = [');
    for (const tool of spec.tools) {
      lines.push(`    ToolSpec(`);
      lines.push(`        name="${tool}",`);
      lines.push(`        description="TODO: describe ${tool}",`);
      lines.push(`        parameters={"type": "object", "properties": {}},`);
      lines.push(`        handler=${tool}_handler,`);
      lines.push(`    ),`);
    }
    lines.push(']');
    lines.push('');
  }

  // MCP connections
  if (spec.mcp_servers && spec.mcp_servers.length > 0) {
    lines.push('# Connect MCP servers');
    lines.push('async def setup_mcp():');
    for (const server of spec.mcp_servers) {
      lines.push(`    ${server}_tools = await McpClient.connect(["python", "-m", "mcp_${server}"])`);
    }
    lines.push('');
  }

  // Strategy
  const strategyClassMap: Record<string, string> = {
    react: 'ReActStrategy()',
    lats: 'LATSStrategy()',
    tree_of_thoughts: 'TreeOfThoughtsStrategy()',
    self_correction: 'SelfCorrectionStrategy()',
  };

  // Agent instantiation
  lines.push('# Create the agent');
  lines.push(`agent = UniversalAgent(`);
  lines.push(`    name="${spec.name}",`);
  lines.push(`    model="${spec.model}",`);

  // System prompt — handle multiline
  if (spec.system_prompt) {
    if (spec.system_prompt.includes('\n') || spec.system_prompt.length > 60) {
      lines.push(`    system_prompt=(`);
      lines.push(`        "${spec.system_prompt.replace(/"/g, '\\"').replace(/\n/g, '\\n')}"`);
      lines.push(`    ),`);
    } else {
      lines.push(`    system_prompt="${spec.system_prompt.replace(/"/g, '\\"')}",`);
    }
  }

  if (spec.strategy && spec.strategy !== 'react') {
    lines.push(`    strategy=${strategyClassMap[spec.strategy] ?? `"${spec.strategy}"`},`);
  }

  if (spec.tools && spec.tools.length > 0) {
    lines.push('    tools=tools,');
  }

  if (spec.temperature !== undefined && spec.temperature !== 0.7) {
    lines.push(`    temperature=${spec.temperature},`);
  }
  if (spec.max_tokens) {
    lines.push(`    max_tokens=${spec.max_tokens},`);
  }
  if (spec.max_iterations && spec.max_iterations !== 10) {
    lines.push(`    max_iterations=${spec.max_iterations},`);
  }

  lines.push(')');
  lines.push('');

  // Usage example
  lines.push('# Use the agent');
  lines.push('async def main():');
  lines.push('    response = await agent.chat("Hello!")');
  lines.push('    print(response)');
  lines.push('');
  lines.push('');
  lines.push('if __name__ == "__main__":');
  lines.push('    import asyncio');
  lines.push('    asyncio.run(main())');

  return lines.join('\n');
}

function ExportCodeModal({
  agentName,
  onClose,
}: {
  agentName: string;
  onClose: () => void;
}) {
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    playgroundApi
      .getAgentSpec(agentName)
      .then((spec) => {
        setCode(generatePythonCode(spec));
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [agentName]);

  function handleCopy() {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-bg-surface rounded-xl border border-border shadow-xl w-[640px] max-w-[90vw] max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border shrink-0">
          <h3 className="text-sm font-semibold m-0 flex items-center gap-2">
            <Code2 size={16} />
            Export as Python Code
          </h3>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleCopy}
              disabled={!code}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-md text-[12px] font-medium bg-primary/10 hover:bg-primary/20 border-none cursor-pointer text-primary transition-colors disabled:opacity-50"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1 rounded hover:bg-bg-subtle border-none cursor-pointer text-text-muted hover:text-text-primary bg-transparent transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="p-8 text-center text-text-muted text-sm">Loading agent config...</div>
          )}
          {error && (
            <div className="p-5 text-error text-sm">{error}</div>
          )}
          {code && (
            <div className="text-[13px]">
              <SyntaxHighlighter
                style={oneDark}
                language="python"
                customStyle={{
                  margin: 0,
                  borderRadius: 0,
                  padding: '20px 24px',
                  fontSize: '12.5px',
                  lineHeight: '1.6',
                }}
                showLineNumbers
              >
                {code}
              </SyntaxHighlighter>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-border text-[11px] text-text-muted shrink-0">
          Uses the <strong>sagewai</strong> SDK. Install with: <code className="px-1 py-0.5 rounded bg-bg-subtle text-[11px]">uv add sagewai</code>
        </div>
      </div>
    </div>
  );
}

function SaveAsModal({
  agentName,
  onSaved,
  onClose,
}: {
  agentName: string;
  onSaved: (newName: string) => void;
  onClose: () => void;
}) {
  const [newName, setNewName] = useState(`${agentName}-copy`);
  const [newModel, setNewModel] = useState('');
  const [models, setModels] = useState<AvailableModel[]>([]);
  const [existingAgents, setExistingAgents] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [spec, setSpec] = useState<AgentSpec | null>(null);

  const nameExists = existingAgents.includes(newName.trim());

  useEffect(() => {
    Promise.all([
      playgroundApi.getAgentSpec(agentName),
      adminApi.listAvailableModels(),
      playgroundApi.listAgents(),
    ])
      .then(([agentSpec, availableModels, agents]) => {
        setSpec(agentSpec);
        setModels(availableModels);
        setNewModel(agentSpec.model);
        setExistingAgents(agents.map((a) => a.name));
      })
      .catch((e) => setError(String(e)));
  }, [agentName]);

  // Auto-update name suffix when model changes
  function handleModelChange(modelId: string) {
    setNewModel(modelId);
    // Extract short model name for suffix (e.g. "gpt-4o-mini" from "gpt-4o-mini")
    const shortModel = modelId.split('/').pop() ?? modelId;
    setNewName(`${agentName}-${shortModel}`);
  }

  async function handleSave() {
    if (!spec || !newName.trim()) return;
    setSaving(true);
    setError('');
    try {
      const cloned: AgentSpec = {
        ...spec,
        name: newName.trim(),
        model: newModel || spec.model,
      };
      await playgroundApi.createAgent(cloned);
      onSaved(newName.trim());
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-bg-surface rounded-xl border border-border shadow-xl w-[400px] max-w-[90vw]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
          <h3 className="text-sm font-semibold m-0">Clone Agent to Registry</h3>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-bg-subtle border-none cursor-pointer text-text-muted hover:text-text-primary bg-transparent transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 flex flex-col gap-3">
          <p className="text-[12px] text-text-muted m-0">
            Clone <strong>{agentName}</strong> with a new name and optionally a different model.
            The new agent will be registered in the{' '}
            <a href="/agents" className="text-primary hover:underline">Agent Registry</a>.
          </p>

          <label className="text-[13px] text-text-secondary">
            Agent Name
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className={`block w-full mt-1 px-2.5 py-[7px] rounded-md border text-[13px] bg-bg-surface box-border ${
                nameExists ? 'border-warning' : 'border-border'
              }`}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSave();
                if (e.key === 'Escape') onClose();
              }}
            />
            {nameExists && (
              <span className="text-[11px] text-warning mt-0.5 block">
                An agent with this name already exists and will be overwritten.
              </span>
            )}
          </label>

          <label className="text-[13px] text-text-secondary">
            Model
            {models.length > 0 ? (
              <select
                value={newModel}
                onChange={(e) => handleModelChange(e.target.value)}
                className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface"
              >
                {Object.entries(
                  models.reduce<Record<string, AvailableModel[]>>((acc, m) => {
                    (acc[m.provider] ??= []).push(m);
                    return acc;
                  }, {}),
                ).map(([provider, providerModels]) => (
                  <optgroup
                    key={provider}
                    label={provider.charAt(0).toUpperCase() + provider.slice(1)}
                  >
                    {providerModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            ) : (
              <div className="mt-1 text-[12px] text-text-muted">Loading models...</div>
            )}
          </label>

          {spec && (
            <div className="text-[11px] text-text-muted bg-bg-subtle rounded-md px-3 py-2 flex flex-col gap-0.5">
              <div>Strategy: <span className="text-text-secondary">{spec.strategy}</span></div>
              <div>Tools: <span className="text-text-secondary">{spec.tools?.join(', ') || 'none'}</span></div>
              <div>Temp: <span className="text-text-secondary">{spec.temperature}</span></div>
            </div>
          )}

          {error && <div className="text-error text-[12px]">{error}</div>}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-border flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-md border border-border text-[13px] bg-transparent cursor-pointer hover:bg-bg-subtle transition-colors text-text-secondary"
          >
            Cancel
          </button>
          <Button onClick={handleSave} disabled={saving || !newName.trim() || !spec}>
            {saving ? 'Saving...' : nameExists ? 'Overwrite & Save' : 'Clone Agent'}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function PlaygroundView() {
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [defaults, setDefaults] = useState<AgentConfigDefaults | undefined>(undefined);
  const [ready, setReady] = useState(false);
  const [configOpen, setConfigOpen] = useState(true);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const searchParams = useSearchParams();

  useEffect(() => {
    const templateId = searchParams.get('template');
    if (templateId) {
      Promise.all([
        adminApi.getAgentTemplate(templateId),
        adminApi.listAvailableModels(),
      ])
        .then(([t, models]) => {
          // Adapt template model if it's not available
          let templateModel = t.model;
          if (models.length > 0 && !models.some((m: AvailableModel) => m.id === templateModel)) {
            const fallback = models.find((m: AvailableModel) => m.supports_tools) ?? models[0];
            templateModel = fallback.id;
          }
          setDefaults({
            name: t.id,
            model: templateModel,
            system_prompt: t.system_prompt,
            temperature: t.temperature,
            strategy: t.strategy,
            tools: t.tools,
            mcp_servers: t.mcp_servers,
            memory_backends: t.memory_backends,
            guardrails: t.guardrails,
          });
        })
        .catch(() => {})
        .finally(() => setReady(true));
    } else {
      setReady(true);
    }
  }, [searchParams]);

  function handleAgentCreated(name: string) {
    setActiveAgent(name);
    setConfigOpen(false);
  }

  function handleSaveAs(newName: string) {
    setSaveAsOpen(false);
    setActiveAgent(newName);
  }

  if (!ready) return null;

  return (
    <div className="flex gap-0 h-[calc(100vh-10rem)] min-h-[500px] relative">
      {/* Left panel — agent config (collapsible) */}
      <div
        className={`shrink-0 transition-all duration-200 ease-in-out overflow-hidden border-r border-border ${
          configOpen ? 'w-[320px]' : 'w-0'
        }`}
      >
        <div className="w-[320px] h-full overflow-y-auto overscroll-contain pr-3 pb-4">
          <AgentConfigPanel
            key={defaults?.name ?? 'default'}
            onAgentCreated={handleAgentCreated}
            defaults={defaults}
          />
          {activeAgent && (
            <div className="mt-3 px-3.5 py-2.5 bg-success-light rounded-lg border border-success/30 text-[13px] text-success">
              <div className="flex items-center justify-between">
                <span>
                  Active: <strong>{activeAgent}</strong>
                </span>
              </div>
              <div className="flex gap-1.5 mt-2">
                <button
                  type="button"
                  onClick={() => setSaveAsOpen(true)}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium bg-success/15 hover:bg-success/25 border-none cursor-pointer text-success transition-colors"
                  title="Clone this agent to the registry with a different name or model"
                >
                  <Copy size={12} />
                  Clone Agent
                </button>
                <button
                  type="button"
                  onClick={() => setExportOpen(true)}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium bg-success/15 hover:bg-success/25 border-none cursor-pointer text-success transition-colors"
                  title="Export agent as Python code"
                >
                  <Code2 size={12} />
                  Export Code
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Toggle button */}
      <button
        type="button"
        onClick={() => setConfigOpen(!configOpen)}
        className="shrink-0 w-6 flex items-center justify-center bg-bg-subtle hover:bg-bg-surface border-none cursor-pointer text-text-muted hover:text-text-primary transition-colors"
        title={configOpen ? 'Collapse config' : 'Show config'}
      >
        {configOpen ? <ChevronLeft size={14} /> : <Settings2 size={14} />}
      </button>

      {/* Right panel — chat */}
      <div className="flex-1 min-w-0 h-full">
        <SSEChat
          agentName={activeAgent}
          onSaveAs={activeAgent ? () => setSaveAsOpen(true) : undefined}
          onExportCode={activeAgent ? () => setExportOpen(true) : undefined}
        />
      </div>

      {/* Save As modal */}
      {saveAsOpen && activeAgent && (
        <SaveAsModal
          agentName={activeAgent}
          onSaved={handleSaveAs}
          onClose={() => setSaveAsOpen(false)}
        />
      )}

      {/* Export Code modal */}
      {exportOpen && activeAgent && (
        <ExportCodeModal
          agentName={activeAgent}
          onClose={() => setExportOpen(false)}
        />
      )}
    </div>
  );
}
