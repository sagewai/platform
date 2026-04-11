'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { playgroundApi, readSSE } from '@/utils/playground-api';
import type { ValidationResult, WorkflowTemplate } from '@/utils/playground-api';
import { adminApi } from '@/utils/api';
import { authFetch } from '@/utils/auth';
import type { AgentSummary, AvailableModel } from '@/utils/types';
import { PipelineGraph } from './pipeline-graph';
import { WorkflowStepCard } from './workflow-step-card';
import { WorkflowAgentPanel } from './workflow-agent-panel';
import { Button, Card, Select, TextInput, TextArea, Badge, useToast } from '@/components/ui/legacy';
import { ShareButton } from './share-button';
import { useWorkflowRun } from '@/hooks/use-workflow-run';
import { Copy, Download, Save, FolderOpen } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type {
  WorkflowDefinition,
  WorkflowNode,
  AgentNodeDef,
  SequentialNode,
} from '@/utils/workflow-types';
import {
  emptyWorkflow,
  workflowToYaml,
  yamlToWorkflow,
  isSequentialNode,
} from '@/utils/workflow-types';

const BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace('/admin', '') ??
  'http://localhost:8000';

type Mode = 'visual' | 'yaml';

export function WorkflowEditor() {
  const { toast } = useToast();
  /* ─── shared state ─── */
  const [mode, setMode] = useState<Mode>('visual');
  const [yaml, setYaml] = useState('');
  const [definition, setDefinition] = useState<WorkflowDefinition>(emptyWorkflow());
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [registeredAgents, setRegisteredAgents] = useState<AgentSummary[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [testInput, setTestInput] = useState('');
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState<{ event: string; data: string }[]>([]);
  const [registryName, setRegistryName] = useState<string | null>(null);
  const [registryVersion, setRegistryVersion] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    playgroundApi.listTemplates().then(setTemplates).catch(() => {});
    adminApi.listAgents().then(setRegisteredAgents).catch(() => {});
    adminApi.listAvailableModels().then(setAvailableModels).catch(() => {});

    // Load from registry if ?load= param is present
    const params = new URLSearchParams(window.location.search);
    const loadName = params.get('load');
    if (loadName) {
      adminApi.getSavedWorkflowByName(loadName).then((wf) => {
        setYaml(wf.yaml_content);
        const parsed = yamlToWorkflow(wf.yaml_content);
        if (parsed) setDefinition(parsed);
        setRegistryName(wf.name);
        setRegistryVersion(wf.version);
        validate(wf.yaml_content);
      }).catch(() => {});
    }
  }, []);

  async function saveToRegistry() {
    const yamlToSave = mode === 'visual' ? generatedYaml : yaml;
    if (!yamlToSave.trim()) {
      toast('error', 'Nothing to save');
      return;
    }
    const name = registryName || definition.name || prompt('Workflow name:');
    if (!name) return;

    setSaving(true);
    try {
      const result = await adminApi.saveWorkflow({
        name,
        yaml_content: yamlToSave,
        description: definition.description || '',
      });
      setRegistryName(result.name);
      setRegistryVersion(result.version);
      toast('success', `Saved "${name}" (v${result.version})`);
    } catch (e: any) {
      toast('error', `Save failed: ${e?.message || 'Unknown error'}`);
    } finally {
      setSaving(false);
    }
  }

  /* ─── validation (shared) ─── */
  const validate = useCallback((yamlStr: string) => {
    if (!yamlStr.trim()) {
      setValidation(null);
      return;
    }
    playgroundApi
      .validateWorkflow(yamlStr)
      .then(setValidation)
      .catch(() => setValidation({ valid: false, error: 'Validation request failed' }));
  }, []);

  /* ─── YAML mode handlers ─── */
  function handleYamlChange(value: string) {
    setYaml(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => validate(value), 300);
  }

  /* ─── Visual mode: definition → auto-generate YAML + validate ─── */
  const generatedYaml = useMemo(() => {
    if (!definition.name) return '';
    return workflowToYaml(definition);
  }, [definition]);

  useEffect(() => {
    if (mode === 'visual' && generatedYaml) {
      setYaml(generatedYaml);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => validate(generatedYaml), 300);
    }
  }, [mode, generatedYaml, validate]);

  /* ─── Mode switching ─── */
  function switchToVisual() {
    const parsed = yamlToWorkflow(yaml);
    if (parsed) {
      setDefinition(parsed);
      setMode('visual');
    } else if (!yaml.trim()) {
      setDefinition(emptyWorkflow());
      setMode('visual');
    } else {
      // Can't parse — warn and stay in YAML mode
      setValidation({ valid: false, error: 'Cannot parse YAML into visual builder — fix syntax or stay in YAML mode' });
    }
  }

  function switchToYaml() {
    if (definition.name) {
      setYaml(workflowToYaml(definition));
    }
    setMode('yaml');
  }

  /* ─── Template loading ─── */
  function loadTemplate(tmpl: WorkflowTemplate) {
    const parsed = yamlToWorkflow(tmpl.yaml);
    if (parsed && availableModels.length > 0) {
      // Replace any template models that aren't available with the first available model
      const availableIds = new Set(availableModels.map((m) => m.id));
      const fallbackModel = availableModels[0];
      for (const [, agentDef] of Object.entries(parsed.agents)) {
        if (agentDef.model && !agentDef.ref && !availableIds.has(agentDef.model)) {
          agentDef.model = fallbackModel.id;
          agentDef.api_base = fallbackModel.api_base || undefined;
        }
      }
      if (parsed.default_model && !availableIds.has(parsed.default_model)) {
        parsed.default_model = fallbackModel.id;
      }
      setDefinition(parsed);
      const adaptedYaml = workflowToYaml(parsed);
      setYaml(adaptedYaml);
      validate(adaptedYaml);
    } else {
      setYaml(tmpl.yaml);
      if (parsed) setDefinition(parsed);
      validate(tmpl.yaml);
    }
  }

  /* ─── Visual builder helpers ─── */
  const agentNames = useMemo(() => Object.keys(definition.agents), [definition]);

  const workflowSteps: WorkflowNode[] = useMemo(() => {
    if (isSequentialNode(definition.workflow)) return definition.workflow.steps;
    return [definition.workflow];
  }, [definition.workflow]);

  function setSteps(steps: WorkflowNode[]) {
    setDefinition((d) => ({
      ...d,
      workflow: { type: 'sequential', steps } as SequentialNode,
    }));
  }

  function updateStep(i: number, node: WorkflowNode) {
    const next = [...workflowSteps];
    next[i] = node;
    setSteps(next);
  }

  function removeStep(i: number) {
    setSteps(workflowSteps.filter((_, j) => j !== i));
  }

  function moveStep(from: number, to: number) {
    const next = [...workflowSteps];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setSteps(next);
  }

  function addStep(type: 'agent' | 'parallel' | 'loop') {
    const node: WorkflowNode =
      type === 'agent'
        ? { agent: agentNames[0] ?? '' }
        : type === 'parallel'
          ? { type: 'parallel', agents: [] }
          : { type: 'loop', agent: agentNames[0] ?? '', max_iterations: 3 };
    setSteps([...workflowSteps, node]);
  }

  /* ─── Result state ─── */
  const [resultTab, setResultTab] = useState<'output' | 'events' | 'stats'>('output');
  const [resultData, setResultData] = useState<{
    output?: string;
    elapsed_seconds?: number;
    agents?: { name: string; model: string; input_tokens: number; output_tokens: number; total_tokens: number; duration_ms?: number; llm_calls?: number }[];
    total_input_tokens?: number;
    total_output_tokens?: number;
    total_tokens?: number;
    llm_call_log?: { agent: string; model: string; input_tokens: number; output_tokens: number; duration_ms: number; cost_usd: number }[];
  } | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  // Durable workflow tracking via SSE + polling
  const durableRun = useWorkflowRun(activeRunId);

  // Sync durable run state into events/result
  useEffect(() => {
    if (!activeRunId) return;

    // Map hook events to display events
    if (durableRun.events.length > 0) {
      setEvents(
        durableRun.events.map((e) => ({
          event: e.type,
          data: JSON.stringify(e.data),
        })),
      );
    }

    if (durableRun.status === 'running') {
      setRunning(true);
    }

    if (durableRun.status === 'completed') {
      if (durableRun.output) {
        const out = durableRun.output as Record<string, unknown>;
        // Backend may return output as a raw string or as { output: "..." }
        const outputText = typeof out === 'string'
          ? out
          : typeof out.output === 'string'
            ? out.output
            : JSON.stringify(out);
        setResultData({
          output: outputText,
          elapsed_seconds: (out.elapsed_seconds as number) ?? undefined,
          total_tokens: (out.total_tokens as number) ?? undefined,
          total_input_tokens: (out.total_input_tokens as number) ?? undefined,
          total_output_tokens: (out.total_output_tokens as number) ?? undefined,
          agents: (out.agents ?? undefined) as { name: string; model: string; input_tokens: number; output_tokens: number; total_tokens: number; duration_ms?: number; llm_calls?: number }[] | undefined,
          llm_call_log: (out.llm_call_log ?? undefined) as { agent: string; model: string; input_tokens: number; output_tokens: number; duration_ms: number; cost_usd: number }[] | undefined,
        });
      }
      setRunning(false);
    }

    if (durableRun.error) {
      setEvents((prev) => [...prev, { event: 'workflow_error', data: JSON.stringify({ error: durableRun.error }) }]);
      setRunning(false);
    }

    if (durableRun.status === 'cancelled' || durableRun.status === 'failed') {
      setRunning(false);
    }
  }, [activeRunId, durableRun.events, durableRun.output, durableRun.error, durableRun.status]);

  /* ─── Run workflow (shared) ─── */
  async function runWorkflow() {
    const yamlToRun = mode === 'visual' ? generatedYaml : yaml;
    if (!yamlToRun.trim() || !testInput.trim() || running) return;
    if (!validation?.valid) return;

    setRunning(true);
    setEvents([]);
    setResultData(null);
    setResultTab('output');
    setActiveRunId(null);

    try {
      // Try durable submit first
      const result = await adminApi.submitWorkflow(yamlToRun, testInput);
      // Durable mode returned a run_id — track it via the hook
      setActiveRunId(result.run_id);
      toast('success', `Workflow submitted (${result.workflow_name})`);
    } catch {
      // Durable submit failed — fall back to inline SSE
      try {
        const resp = await authFetch(`${BASE}/workflows/run`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ yaml: yamlToRun, message: testInput }),
        });

        if (!resp.ok) {
          const err = await resp.text();
          setEvents([{ event: 'error', data: err }]);
          setRunning(false);
          return;
        }

        // Check if response is JSON (durable) or SSE (inline)
        const contentType = resp.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const data = await resp.json();
          setActiveRunId(data.run_id);
          return;
        }

        // SSE inline fallback
        for await (const evt of readSSE(resp)) {
          setEvents((prev) => [...prev, evt]);
          if (evt.event === 'workflow_finished') {
            try {
              const parsed = JSON.parse(evt.data);
              // Inline mode returns { output, agents, total_tokens, ... }
              // Durable mode returns a raw string
              if (typeof parsed === 'string') {
                setResultData({ output: parsed });
              } else {
                setResultData(parsed);
              }
            } catch {
              // Unparseable — use raw data as output text
              setResultData({ output: evt.data });
            }
          }
        }
        setRunning(false);
      } catch (e) {
        const detail = e instanceof TypeError
          ? `${String(e)} — check browser console for CORS errors. Try a hard refresh (Cmd+Shift+R).`
          : String(e);
        setEvents((prev) => [...prev, { event: 'error', data: detail }]);
        setRunning(false);
      }
    }
  }

  /* ─── Render ─── */
  return (
    <div className="flex gap-5 min-h-[500px] items-start">
      {/* Left panel — editor */}
      <div className="w-1/2 flex flex-col gap-3">
        {/* Mode toggle + templates */}
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex rounded-md border border-border overflow-hidden">
            <button
              type="button"
              onClick={() => mode === 'yaml' ? switchToVisual() : undefined}
              className={`px-3 py-1.5 text-xs border-none cursor-pointer transition-colors ${
                mode === 'visual'
                  ? 'bg-primary text-white'
                  : 'bg-bg-surface text-text-muted hover:text-text-primary'
              }`}
            >
              Visual
            </button>
            <button
              type="button"
              onClick={() => mode === 'visual' ? switchToYaml() : undefined}
              className={`px-3 py-1.5 text-xs border-none border-l border-border cursor-pointer transition-colors ${
                mode === 'yaml'
                  ? 'bg-primary text-white'
                  : 'bg-bg-surface text-text-muted hover:text-text-primary'
              }`}
            >
              YAML
            </button>
          </div>

          <span className="text-[13px] text-text-muted ml-2">Templates:</span>
          {templates.map((t) => (
            <Button
              key={t.name}
              variant="secondary"
              onClick={() => loadTemplate(t)}
              title={t.description}
              className="text-xs"
            >
              {t.name}
            </Button>
          ))}

          <div className="ml-auto flex items-center gap-2">
            {registryName && (
              <span className="text-xs text-text-muted">
                {registryName} v{registryVersion}
              </span>
            )}
            <Button
              variant="secondary"
              onClick={saveToRegistry}
              disabled={saving}
              className="text-xs flex items-center gap-1"
              title="Save to workflow registry"
            >
              <Save className="w-3.5 h-3.5" />
              {saving ? 'Saving...' : 'Save'}
            </Button>
          </div>
        </div>

        {/* ═══ VISUAL MODE ═══ */}
        {mode === 'visual' && (
          <div className="flex flex-col gap-3">
            {/* Name + Description */}
            <Card className="p-3 flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-text-muted w-24 shrink-0">Name</span>
                <TextInput
                  value={definition.name}
                  onChange={(e) => setDefinition((d) => ({ ...d, name: e.target.value }))}
                  placeholder="my-workflow"
                />
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-text-muted w-24 shrink-0">Description</span>
                <TextInput
                  value={definition.description}
                  onChange={(e) => setDefinition((d) => ({ ...d, description: e.target.value }))}
                  placeholder="What this workflow does..."
                />
              </div>
            </Card>

            {/* Workflow defaults (collapsible) */}
            <details className="group">
              <summary className="text-[12px] text-text-muted cursor-pointer select-none hover:text-text-primary transition-colors list-none flex items-center gap-1">
                <span className="text-[10px] group-open:rotate-90 transition-transform">▶</span>
                Workflow Defaults
              </summary>
              <Card className="p-3 mt-2 flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-text-muted w-28 shrink-0">Default Model</span>
                  <select
                    value={definition.default_model ?? ''}
                    onChange={(e) =>
                      setDefinition((d) => ({ ...d, default_model: e.target.value || undefined }))
                    }
                    className="flex-1 px-2 py-1.5 rounded border border-border text-xs bg-bg-surface"
                  >
                    <option value="">None (use agent model)</option>
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
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] text-text-muted">Fallback Models</span>
                  {(definition.fallback_models ?? []).map((m, i) => (
                    <div key={`${m}-${i}`} className="flex items-center gap-1.5 ml-1">
                      <span className="text-[10px] text-text-muted w-4">#{i + 1}</span>
                      <span className="text-xs font-[family-name:var(--font-mono)] flex-1">{m}</span>
                      <button
                        type="button"
                        onClick={() =>
                          setDefinition((d) => ({
                            ...d,
                            fallback_models: (d.fallback_models ?? []).filter((_, j) => j !== i),
                          }))
                        }
                        className="text-[10px] text-error/70 hover:text-error border-none bg-transparent cursor-pointer"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  <select
                    value=""
                    onChange={(e) => {
                      if (e.target.value) {
                        setDefinition((d) => ({
                          ...d,
                          fallback_models: [...(d.fallback_models ?? []), e.target.value],
                        }));
                        e.target.value = '';
                      }
                    }}
                    className="text-xs px-2 py-1 rounded border border-border bg-bg-surface"
                  >
                    <option value="">+ Add fallback model...</option>
                    {availableModels
                      .filter((m) => !(definition.fallback_models ?? []).includes(m.id))
                      .map((m) => (
                        <option key={m.id} value={m.id}>{m.id} ({m.provider})</option>
                      ))}
                  </select>
                </div>
              </Card>
            </details>

            {/* Agents section */}
            <div>
              <div className="text-[12px] text-text-muted font-semibold mb-1.5 uppercase tracking-wide">
                Agents
              </div>
              <WorkflowAgentPanel
                agents={definition.agents}
                registeredAgents={registeredAgents}
                availableModels={availableModels}
                onChange={(agents) => setDefinition((d) => ({ ...d, agents }))}
              />
            </div>

            {/* Steps section */}
            <div>
              <div className="text-[12px] text-text-muted font-semibold mb-1.5 uppercase tracking-wide">
                Steps
              </div>
              <div className="flex flex-col gap-2">
                {workflowSteps.map((step, i) => (
                  <WorkflowStepCard
                    key={i}
                    index={i}
                    node={step}
                    agentNames={agentNames}
                    onChange={(n) => updateStep(i, n)}
                    onRemove={() => removeStep(i)}
                    onMoveUp={i > 0 ? () => moveStep(i, i - 1) : undefined}
                    onMoveDown={i < workflowSteps.length - 1 ? () => moveStep(i, i + 1) : undefined}
                  />
                ))}
                <div className="flex gap-1.5">
                  <Button variant="secondary" className="text-xs" onClick={() => addStep('agent')}>
                    + Agent Step
                  </Button>
                  <Button variant="secondary" className="text-xs" onClick={() => addStep('parallel')}>
                    + Parallel Block
                  </Button>
                  <Button variant="secondary" className="text-xs" onClick={() => addStep('loop')}>
                    + Loop Block
                  </Button>
                </div>
              </div>
            </div>

            {/* Generated YAML preview */}
            {generatedYaml && (
              <details>
                <summary className="text-[12px] text-text-muted cursor-pointer select-none hover:text-text-primary transition-colors list-none flex items-center gap-1">
                  <span className="text-[10px]">▶</span>
                  Generated YAML
                </summary>
                <pre className="mt-2 p-3 rounded-lg bg-bg-subtle border border-border text-[12px] font-[family-name:var(--font-mono)] leading-relaxed overflow-auto max-h-[250px] whitespace-pre-wrap">
                  {generatedYaml}
                </pre>
              </details>
            )}
          </div>
        )}

        {/* ═══ YAML MODE ═══ */}
        {mode === 'yaml' && (
          <>
            <textarea
              ref={textareaRef}
              value={yaml}
              onChange={(e) => handleYamlChange(e.target.value)}
              placeholder={`name: my-workflow\nagents:\n  # Inline agent with context & directives\n  agent-a:\n    model: gpt-4o\n    system_prompt: ...\n    context:\n      scopes: [org, project]\n      top_k: 10\n      strategy: hybrid\n    directives: |\n      @context('relevant background')\n  # Reference existing playground agent\n  agent-b:\n    ref: my-playground-agent\nworkflow:\n  type: sequential\n  steps:\n    - agent: agent-a\n    - agent: agent-b`}
              spellCheck={false}
              className="w-full min-h-[380px] p-3.5 rounded-lg border border-border text-[13px] font-[family-name:var(--font-mono)] leading-relaxed resize-y box-border bg-bg-subtle tab-[2]"
            />
          </>
        )}

        {/* Validation status (shared) */}
        {validation && (
          <div
            className={`px-3 py-2 rounded-md text-[13px] border ${
              validation.valid
                ? 'bg-success-light text-success border-success/30'
                : 'bg-error-light text-error border-error/30'
            }`}
          >
            {validation.valid
              ? `Valid: ${validation.name} (${validation.agents?.length ?? 0} agents)`
              : `Invalid: ${validation.error}`}
          </div>
        )}
      </div>

      {/* Right panel — graph + execution */}
      <div className="flex-1 flex flex-col gap-4">
        {/* Pipeline graph */}
        <PipelineGraph validation={validation} definition={mode === 'visual' ? definition : (() => { try { return yamlToWorkflow(yaml); } catch { return null; } })()} />

        {/* Test input + run */}
        <div className="flex gap-2 items-end">
          <textarea
            value={testInput}
            onChange={(e) => setTestInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                runWorkflow();
              }
            }}
            placeholder="Enter a test message for the workflow... (Shift+Enter for new line)"
            disabled={running}
            rows={3}
            className="flex-1 px-3 py-2 rounded-md border border-border text-sm bg-bg-surface outline-none resize-y min-h-[60px] max-h-[200px]"
          />
          <Button
            onClick={runWorkflow}
            disabled={running || !validation?.valid || !testInput.trim()}
          >
            {running ? 'Running...' : 'Run Workflow'}
          </Button>
          {running && activeRunId && (
            <Button
              variant="secondary"
              onClick={() => durableRun.cancel()}
              className="text-error"
            >
              Cancel
            </Button>
          )}
        </div>

        {/* Progress bar for durable runs */}
        {activeRunId && durableRun.stepsTotal && durableRun.stepsTotal > 0 && (
          <div className="flex items-center gap-2">
            <div className="flex-1 h-2 rounded-full bg-bg-subtle overflow-hidden">
              <div
                className="h-full bg-primary rounded-full transition-all duration-300"
                style={{
                  width: `${Math.round((durableRun.stepsCompleted / durableRun.stepsTotal) * 100)}%`,
                }}
              />
            </div>
            <span className="text-[11px] text-text-muted whitespace-nowrap">
              {durableRun.stepsCompleted}/{durableRun.stepsTotal} steps
            </span>
            {durableRun.isTerminal && (
              <a
                href={`/workflows/history/${activeRunId}`}
                className="text-[11px] text-primary hover:underline"
              >
                View in History
              </a>
            )}
          </div>
        )}

        {/* Execution results */}
        {(events.length > 0 || resultData) && (
          <div className="border border-border rounded-lg overflow-hidden">
            {/* Result tabs */}
            <div className="flex border-b border-border bg-bg-subtle">
              {(['output', 'events', 'stats'] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setResultTab(tab)}
                  className={`px-4 py-2 text-xs font-medium border-none cursor-pointer transition-colors ${
                    resultTab === tab
                      ? 'bg-bg-surface text-text-primary border-b-2 border-b-primary'
                      : 'bg-transparent text-text-muted hover:text-text-primary'
                  }`}
                >
                  {tab === 'output' ? 'Output' : tab === 'events' ? 'Event Log' : 'Stats'}
                  {tab === 'stats' && resultData?.total_tokens ? ` (${resultData.total_tokens} tok)` : ''}
                </button>
              ))}
              {resultData?.elapsed_seconds != null && (
                <span className="ml-auto px-3 py-2 text-[11px] text-text-muted self-center">
                  {resultData.elapsed_seconds}s
                </span>
              )}
            </div>

            {/* Output tab — full result rendered as markdown */}
            {resultTab === 'output' && (
              <div className="p-4 max-h-[500px] overflow-auto">
                {resultData?.output ? (
                  <>
                    <div className="flex gap-1.5 mb-3">
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(resultData.output ?? '');
                          toast('success', 'Copied to clipboard');
                        }}
                        className="flex items-center gap-1 text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                      >
                        <Copy size={12} />
                        Copy
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const blob = new Blob([resultData.output ?? ''], { type: 'text/markdown' });
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement('a');
                          a.href = url;
                          a.download = `workflow-output.md`;
                          a.click();
                          URL.revokeObjectURL(url);
                        }}
                        className="flex items-center gap-1 text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                      >
                        <Download size={12} />
                        Download
                      </button>
                      <ShareButton
                        agentName={validation?.name ?? 'workflow'}
                        inputText={testInput}
                        outputText={resultData?.output ?? ''}
                        source="workflow"
                      />
                    </div>
                    <div className="prose prose-sm prose-invert max-w-none text-text-primary [&_pre]:bg-[#111827] [&_pre]:p-3 [&_pre]:rounded [&_code]:text-xs [&_code]:font-[family-name:var(--font-mono)] [&_table]:text-xs [&_th]:px-2 [&_td]:px-2">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {resultData.output}
                      </ReactMarkdown>
                    </div>
                  </>
                ) : running ? (
                  <div className="text-sm text-text-muted">Running workflow...</div>
                ) : events.some((e) => e.event === 'error' || e.event === 'workflow_error') ? (
                  <div className="text-sm text-error">
                    {events.filter((e) => e.event === 'error' || e.event === 'workflow_error').map((e) => e.data).join('\n')}
                  </div>
                ) : (
                  <div className="text-sm text-text-muted">Waiting for result...</div>
                )}
              </div>
            )}

            {/* Events tab — raw SSE log */}
            {resultTab === 'events' && (
              <div className="bg-[#111827] p-4 max-h-[500px] overflow-auto font-[family-name:var(--font-mono)] text-xs leading-[1.8]">
                {events.map((evt, i) => {
                  let parsedData = evt.data;
                  try {
                    const obj = JSON.parse(evt.data);
                    parsedData = JSON.stringify(obj, null, 2);
                  } catch {
                    // keep raw
                  }

                  return (
                    <div key={i}>
                      <span
                        className={
                          evt.event === 'workflow_finished'
                            ? 'text-success'
                            : evt.event === 'workflow_error' || evt.event === 'error'
                              ? 'text-error'
                              : 'text-info'
                        }
                      >
                        {evt.event}
                      </span>
                      <span className="text-text-muted"> &mdash; </span>
                      <span className="text-[#d1d5db] whitespace-pre-wrap">{parsedData}</span>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Stats tab — per-agent breakdown */}
            {resultTab === 'stats' && (
              <div className="p-4 max-h-[500px] overflow-auto">
                {resultData ? (
                  <div className="flex flex-col gap-3">
                    {/* Summary */}
                    <div className="grid grid-cols-3 gap-3">
                      <div className="bg-bg-subtle rounded-lg p-3 text-center">
                        <div className="text-lg font-semibold text-text-primary">{resultData.elapsed_seconds ?? '—'}s</div>
                        <div className="text-[11px] text-text-muted">Duration</div>
                      </div>
                      <div className="bg-bg-subtle rounded-lg p-3 text-center">
                        <div className="text-lg font-semibold text-text-primary">{resultData.total_tokens?.toLocaleString() ?? '—'}</div>
                        <div className="text-[11px] text-text-muted">Total Tokens</div>
                      </div>
                      <div className="bg-bg-subtle rounded-lg p-3 text-center">
                        <div className="text-lg font-semibold text-text-primary">{resultData.agents?.length ?? 0}</div>
                        <div className="text-[11px] text-text-muted">Agents Used</div>
                      </div>
                    </div>

                    {/* Token breakdown */}
                    {resultData.total_tokens != null && resultData.total_tokens > 0 && (
                      <div className="text-xs text-text-muted">
                        Input: {resultData.total_input_tokens?.toLocaleString()} &middot; Output: {resultData.total_output_tokens?.toLocaleString()}
                      </div>
                    )}

                    {/* Per-agent table */}
                    {resultData.agents && resultData.agents.length > 0 && (
                      <div className="border border-border rounded-lg overflow-hidden">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="bg-bg-subtle text-text-muted">
                              <th className="text-left px-3 py-2 font-medium">Agent</th>
                              <th className="text-left px-3 py-2 font-medium">Model</th>
                              <th className="text-right px-3 py-2 font-medium">Input</th>
                              <th className="text-right px-3 py-2 font-medium">Output</th>
                              <th className="text-right px-3 py-2 font-medium">Total</th>
                              <th className="text-right px-3 py-2 font-medium">Duration</th>
                              <th className="text-right px-3 py-2 font-medium">Calls</th>
                            </tr>
                          </thead>
                          <tbody>
                            {resultData.agents.map((a) => (
                              <tr key={a.name} className="border-t border-border">
                                <td className="px-3 py-2 font-medium text-text-primary">{a.name}</td>
                                <td className="px-3 py-2 font-[family-name:var(--font-mono)] text-text-muted">{a.model}</td>
                                <td className="px-3 py-2 text-right text-text-muted">{a.input_tokens.toLocaleString()}</td>
                                <td className="px-3 py-2 text-right text-text-muted">{a.output_tokens.toLocaleString()}</td>
                                <td className="px-3 py-2 text-right font-medium">{a.total_tokens.toLocaleString()}</td>
                                <td className="px-3 py-2 text-right text-text-muted">
                                  {a.duration_ms ? `${(a.duration_ms / 1000).toFixed(1)}s` : '\u2014'}
                                </td>
                                <td className="px-3 py-2 text-right text-text-muted">
                                  {a.llm_calls ?? '\u2014'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {/* LLM Call Log */}
                    {resultData.llm_call_log && resultData.llm_call_log.length > 0 && (
                      <div>
                        <div className="text-xs text-text-muted font-semibold mb-1.5 uppercase tracking-wide">
                          LLM Call Log
                        </div>
                        <div className="border border-border rounded-lg overflow-hidden">
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="bg-bg-subtle text-text-muted">
                                <th className="text-left px-3 py-1.5 font-medium">#</th>
                                <th className="text-left px-3 py-1.5 font-medium">Agent</th>
                                <th className="text-left px-3 py-1.5 font-medium">Model</th>
                                <th className="text-right px-3 py-1.5 font-medium">Tokens</th>
                                <th className="text-right px-3 py-1.5 font-medium">Duration</th>
                                <th className="text-right px-3 py-1.5 font-medium">Cost</th>
                              </tr>
                            </thead>
                            <tbody>
                              {resultData.llm_call_log.map((call, i) => (
                                <tr key={i} className="border-t border-border">
                                  <td className="px-3 py-1.5 text-text-muted">{i + 1}</td>
                                  <td className="px-3 py-1.5">{call.agent}</td>
                                  <td className="px-3 py-1.5 font-[family-name:var(--font-mono)] text-text-muted">{call.model}</td>
                                  <td className="px-3 py-1.5 text-right">{(call.input_tokens + call.output_tokens).toLocaleString()}</td>
                                  <td className="px-3 py-1.5 text-right">{call.duration_ms ? `${(call.duration_ms / 1000).toFixed(1)}s` : '\u2014'}</td>
                                  <td className="px-3 py-1.5 text-right text-success">{call.cost_usd ? `$${call.cost_usd.toFixed(4)}` : '\u2014'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-text-muted">Run a workflow to see stats.</div>
                )}
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
