'use client';

import { adminApi } from '@/utils/api';
import { authSSE } from '@/utils/auth';
import type { WorkflowRun, WorkflowEvent } from '@/utils/types';
import Link from 'next/link';
import { Fragment, useEffect, useState, useRef, use } from 'react';
import { useRouter } from 'next/navigation';
import { Card, Badge, Button, Skeleton, ConfirmDialog, useToast } from '@/components/ui/legacy';
import { PipelineGraph } from '@/components/pipeline-graph';
import { ShareButton } from '@/components/share-button';
import { yamlToWorkflow } from '@/utils/workflow-types';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Copy, Download, Play, RotateCcw } from 'lucide-react';
import dynamic from 'next/dynamic';
const ExecutionCanvas = dynamic(
  () => import('@/components/premium/execution-canvas').then((m) => m.ExecutionCanvas),
  { loading: () => <p className="text-sm text-text-muted p-md">Loading canvas...</p> },
);

const ReplayPlayer = dynamic(
  () => import('@/components/premium/replay-player').then((m) => m.ReplayPlayer),
  { loading: () => <p className="text-sm text-text-muted p-md">Loading replay...</p> },
);

const SankeyDiagram = dynamic(
  () => import('@/components/premium/sankey-diagram').then((m) => m.SankeyDiagram),
  { loading: () => <p className="text-sm text-text-muted p-md">Loading cost flow...</p> },
);

const API_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

interface Props {
  params: Promise<{ runId: string }>;
}

/* ─── Structure-aware pipeline flow ─── */

interface AgentStat {
  name: string;
  model: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
}

function AgentBox({ agent, status }: { agent: AgentStat; status: string }) {
  const borderClass =
    status === 'completed' ? 'border-success bg-success/10'
    : status === 'running' ? 'border-info bg-info/10 animate-pulse'
    : status === 'failed' ? 'border-error bg-error/10'
    : 'border-border bg-bg-subtle';
  return (
    <div className={`shrink-0 rounded-lg border-2 p-3 min-w-[140px] text-center ${borderClass}`}>
      <div className="font-semibold text-sm">{agent.name}</div>
      <div className="text-[11px] text-text-muted font-[family-name:var(--font-mono)] mt-1">{agent.model}</div>
      {agent.total_tokens > 0 && (
        <div className="text-[10px] text-text-muted mt-1">{agent.total_tokens.toLocaleString()} tok</div>
      )}
    </div>
  );
}

function FlowArrow({ done }: { done: boolean }) {
  return (
    <div className="flex items-center px-1 shrink-0">
      <div className={`w-8 h-0.5 ${done ? 'bg-success' : 'bg-border'}`} />
      <div className={`w-0 h-0 border-t-[5px] border-t-transparent border-b-[5px] border-b-transparent border-l-[6px] ${done ? 'border-l-success' : 'border-l-border'}`} />
    </div>
  );
}

interface FlowProps {
  agents: AgentStat[];
  workflowNode?: import('@/utils/workflow-types').WorkflowNode;
  runStatus: string;
}

function FlowVisualization({ agents, workflowNode, runStatus }: FlowProps) {
  const agentMap = new Map(agents.map((a) => [a.name, a]));
  const done = runStatus === 'completed';

  // Render a workflow node tree recursively
  function renderNode(node: import('@/utils/workflow-types').WorkflowNode): React.ReactNode {
    // AgentStep
    if ('agent' in node && !('type' in node)) {
      const agent = agentMap.get(node.agent);
      if (!agent) return null;
      return <AgentBox agent={agent} status={done ? 'completed' : runStatus} />;
    }
    // Sequential
    if ('type' in node && node.type === 'sequential') {
      const seq = node as import('@/utils/workflow-types').SequentialNode;
      return (
        <div className="flex items-center gap-0">
          {seq.steps.map((step, i) => (
            <Fragment key={i}>
              {i > 0 && <FlowArrow done={done} />}
              {renderNode(step)}
            </Fragment>
          ))}
        </div>
      );
    }
    // Parallel
    if ('type' in node && node.type === 'parallel') {
      const par = node as import('@/utils/workflow-types').ParallelNode;
      return (
        <div className="flex items-center gap-1">
          <div className="flex flex-col items-center text-info text-[10px] font-semibold"><span>⟨</span><span className="text-[8px]">PAR</span></div>
          <div className="flex flex-col gap-2 border-l-2 border-r-2 border-info/30 px-3 py-2 rounded">
            {par.agents.map((name) => {
              const agent = agentMap.get(name);
              if (!agent) return <div key={name} className="text-xs text-text-muted">{name}</div>;
              return <AgentBox key={name} agent={agent} status={done ? 'completed' : runStatus} />;
            })}
          </div>
          <div className="flex flex-col items-center text-info text-[10px] font-semibold"><span>⟩</span><span className="text-[8px]">merge</span></div>
        </div>
      );
    }
    // Loop
    if ('type' in node && node.type === 'loop') {
      const loop = node as import('@/utils/workflow-types').LoopNode;
      const agent = agentMap.get(loop.agent);
      if (!agent) return null;
      return (
        <div className="border border-warning/30 rounded-lg px-3 py-2 bg-warning/5">
          <AgentBox agent={agent} status={done ? 'completed' : runStatus} />
          <div className="text-[9px] text-warning text-center mt-1">↻ ×{loop.max_iterations}</div>
        </div>
      );
    }
    return null;
  }

  // If we have structured workflow definition, render it
  if (workflowNode) {
    return (
      <div className="flex items-center gap-0 overflow-x-auto pb-2">
        {renderNode(workflowNode)}
      </div>
    );
  }

  // Fallback: flat sequential rendering
  return (
    <div className="flex items-center gap-0 overflow-x-auto pb-2">
      {agents.map((agent, i) => (
        <Fragment key={agent.name}>
          {i > 0 && <FlowArrow done={done} />}
          <AgentBox agent={agent} status={done ? 'completed' : runStatus} />
        </Fragment>
      ))}
    </div>
  );
}

function statusVariant(status: string): 'success' | 'error' | 'info' | 'warning' | 'default' {
  switch (status) {
    case 'completed': return 'success';
    case 'failed': return 'error';
    case 'running': return 'info';
    case 'pending': return 'warning';
    case 'cancelled': return 'default';
    default: return 'default';
  }
}

export default function WorkflowRunDetailPage({ params }: Props) {
  const { runId } = use(params);
  const router = useRouter();
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCancel, setShowCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [replayInput, setReplayInput] = useState('');
  const [replayOpen, setReplayOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [resultTab, setResultTab] = useState<'output' | 'events' | 'stats' | 'costflow' | 'canvas' | 'replay'>('output');
  const { toast } = useToast();
  const abortRef = useRef<AbortController | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch run data
  async function fetchRun() {
    try {
      const data = await adminApi.getWorkflowRun(runId);
      if (data) setRun(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  // Reset state when navigating between runs
  useEffect(() => {
    setRun(null);
    setEvents([]);
    setLoading(true);
    setResultTab('output');
  }, [runId]);

  // Connect SSE for live events (via authSSE, not EventSource)
  useEffect(() => {
    fetchRun();

    function startPolling() {
      if (pollRef.current) return;
      pollRef.current = setInterval(fetchRun, 3000);
    }

    const sseUrl = `${API_BASE}/workflows/runs/${runId}/events`;
    const controller = authSSE(
      sseUrl,
      (type, data) => {
        setEvents((prev) => [...prev, {
          id: prev.length,
          run_id: runId,
          event_type: type,
          data,
          created_at: new Date().toISOString(),
        }]);

        if (type === 'workflow_started') {
          setRun((r) => r ? { ...r, status: 'running' } : r);
        } else if (type === 'step_completed') {
          const stepIdx = (data.step_index ?? data.step) as number | undefined;
          if (stepIdx !== undefined) {
            setRun((r) => r ? { ...r, steps_completed: stepIdx + 1 } : r);
          }
        } else if (type === 'workflow_finished') {
          // Keep the full data dict (contains output, agents, total_tokens, etc.)
          // If fetchRun() already loaded richer data, don't overwrite with SSE data
          setRun((r) => {
            if (!r) return r;
            const sseOutput = typeof data === 'string'
              ? { output: data }
              : (data as Record<string, unknown>);
            // Prefer existing run.output if it's already a rich object (from fetchRun)
            const merged = r.output && typeof r.output === 'object' && 'agents' in r.output
              ? r.output
              : sseOutput;
            return { ...r, status: 'completed', output: merged };
          });
        } else if (type === 'workflow_failed') {
          setRun((r) => r ? { ...r, status: 'failed', error: (data.error as string) ?? 'Unknown error' } : r);
        } else if (type === 'workflow_cancelled') {
          setRun((r) => r ? { ...r, status: 'cancelled' } : r);
        }
      },
      { onError: startPolling },
    );
    abortRef.current = controller;

    return () => {
      controller.abort();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [runId]);

  // Stop polling when run is terminal
  useEffect(() => {
    if (run && ['completed', 'failed', 'cancelled'].includes(run.status)) {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
  }, [run?.status]);

  async function handleCancel() {
    setCancelling(true);
    try {
      await adminApi.cancelWorkflowRun(runId);
      toast('success', 'Run cancelled');
      await fetchRun();
    } catch {
      toast('error', 'Failed to cancel run');
    } finally {
      setCancelling(false);
      setShowCancel(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto">
        <Skeleton lines={1} className="w-32 mb-md" />
        <Skeleton lines={1} className="w-64 mb-lg" />
        <div className="grid grid-cols-3 gap-md"><Skeleton lines={2} /><Skeleton lines={2} /><Skeleton lines={2} /></div>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="max-w-5xl mx-auto">
        <Link href="/workflows/history" className="text-primary no-underline text-sm">&larr; Back to history</Link>
        <h1 className="mt-md text-xl font-bold font-[family-name:var(--font-heading)]">Run not found</h1>
      </div>
    );
  }

  const isActive = run.status === 'running' || run.status === 'pending';
  const progress = run.steps_total
    ? Math.round(((run.steps_completed ?? 0) / run.steps_total) * 100)
    : null;

  // Extract output text for rendering
  const outputText: string | null = (() => {
    if (!run.output) return null;
    if (typeof run.output === 'string') return run.output;
    if (typeof run.output === 'object') {
      const o = run.output as Record<string, unknown>;
      if (typeof o.output === 'string') return o.output;
      return JSON.stringify(run.output, null, 2);
    }
    return String(run.output);
  })();

  // Extract stats from output if available
  const resultStats = (() => {
    if (!run.output || typeof run.output !== 'object') return null;
    const o = run.output as Record<string, unknown>;
    return {
      elapsed_seconds: o.elapsed_seconds as number | undefined,
      total_tokens: o.total_tokens as number | undefined,
      total_input_tokens: o.total_input_tokens as number | undefined,
      total_output_tokens: o.total_output_tokens as number | undefined,
      agents: o.agents as Array<{ name: string; model: string; input_tokens: number; output_tokens: number; total_tokens: number }> | undefined,
    };
  })();

  // Try to parse YAML for pipeline graph — stored in input.yaml, data.yaml, or data.input_data.yaml
  const yamlSpec = (
    (run.input as Record<string, unknown> | undefined)?.yaml ??
    run.data?.yaml ??
    (run.data?.input_data as Record<string, unknown> | undefined)?.yaml
  ) as string | undefined;
  const parsedDef = yamlSpec ? yamlToWorkflow(yamlSpec) : null;
  const validation = yamlSpec
    ? { valid: true, name: run.workflow_name, agents: resultStats?.agents?.map(a => ({ name: a.name })) }
    : null;

  const inputMessage = (() => {
    if (!run.input) return '';
    if (typeof run.input === 'string') return run.input;
    const inp = run.input as Record<string, unknown>;
    return (inp.message as string) || JSON.stringify(run.input, null, 2);
  })();

  function handleCopyOutput() {
    if (outputText) {
      navigator.clipboard.writeText(outputText);
      toast('success', 'Copied to clipboard');
    }
  }

  function handleDownloadOutput() {
    if (!outputText) return;
    const blob = new Blob([outputText], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${run?.workflow_name ?? 'workflow'}-output.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleEditAndRerun() {
    const yamlStr = yamlSpec || '';
    const encoded = encodeURIComponent(yamlStr);
    const msgEncoded = encodeURIComponent(inputMessage);
    window.location.href = `/workflows?yaml=${encoded}&message=${msgEncoded}`;
  }

  async function handleRerunNow() {
    if (!yamlSpec || !inputMessage) return;
    setRerunning(true);
    try {
      const resp = await adminApi.submitWorkflow(yamlSpec, inputMessage);
      toast('success', 'Workflow re-run started');
      router.push(`/workflows/history/${resp.run_id}`);
    } catch {
      toast('error', 'Failed to start re-run');
    } finally {
      setRerunning(false);
    }
  }

  async function handleReplay() {
    if (!yamlSpec || !replayInput.trim()) return;
    setReplaying(true);
    try {
      const resp = await adminApi.submitWorkflow(yamlSpec, replayInput.trim());
      toast('success', 'Replay started with new input');
      router.push(`/workflows/history/${resp.run_id}`);
    } catch {
      toast('error', 'Failed to start replay');
    } finally {
      setReplaying(false);
    }
  }

  return (
    <div className="max-w-5xl mx-auto">
      <Link href="/workflows/history" className="text-primary no-underline text-sm">&larr; Back to history</Link>

      <div className="flex items-center gap-md mt-md mb-lg flex-wrap">
        <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">
          {run.workflow_name}
        </h1>
        <Badge variant={statusVariant(run.status)}>{run.status}</Badge>
        <div className="ml-auto flex items-center gap-2">
          {isActive && (
            <Button variant="danger" size="sm" onClick={() => setShowCancel(true)} disabled={cancelling}>
              Cancel Run
            </Button>
          )}
          {yamlSpec && (
            <>
              <Button variant="primary" size="sm" onClick={handleRerunNow} disabled={rerunning || !inputMessage}>
                <Play size={13} className="mr-1" />
                {rerunning ? 'Starting...' : 'Re-run Now'}
              </Button>
              <Button variant="secondary" size="sm" onClick={handleEditAndRerun}>
                <RotateCcw size={13} className="mr-1" />
                Edit & Re-run
              </Button>
            </>
          )}
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-md mb-lg">
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Run ID</div>
          <div className="text-sm font-semibold mt-1 font-[family-name:var(--font-mono)]">
            {run.run_id.slice(0, 12)}...
          </div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Progress</div>
          <div className="text-sm font-semibold mt-1">
            {run.steps_total ? `${run.steps_completed ?? 0} / ${run.steps_total} steps` : '\u2014'}
          </div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Duration</div>
          <div className="text-sm font-semibold mt-1">
            {resultStats?.elapsed_seconds ? `${resultStats.elapsed_seconds}s` : '\u2014'}
          </div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Total Tokens</div>
          <div className="text-sm font-semibold mt-1">
            {resultStats?.total_tokens ? resultStats.total_tokens.toLocaleString() : '\u2014'}
          </div>
        </Card>
      </div>

      {/* Progress bar */}
      {progress !== null && (
        <div className="mb-lg">
          <div className="flex justify-between text-xs text-text-muted mb-1">
            <span>Step progress</span>
            <span>{progress}%</span>
          </div>
          <div className="w-full h-2 bg-bg-subtle rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Pipeline graph */}
      {(parsedDef || validation) && (
        <div className="mb-lg">
          <PipelineGraph validation={validation} definition={parsedDef} />
        </div>
      )}

      {/* Step flow visualization — structure-aware */}
      {resultStats?.agents && resultStats.agents.length > 0 && (
        <div className="mb-lg">
          <h3 className="text-sm font-semibold text-text-muted uppercase mb-3">Pipeline Flow</h3>
          <FlowVisualization
            agents={resultStats.agents}
            workflowNode={parsedDef?.workflow}
            runStatus={run.status}
          />
        </div>
      )}

      {/* Result tabs — matching the builder output */}
      <div className="border border-border rounded-lg overflow-hidden mb-lg">
        <div className="flex border-b border-border bg-bg-subtle">
          {(['output', 'events', 'stats', 'costflow', 'canvas', 'replay'] as const).map((tab) => (
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
              {tab === 'output' ? 'Output' : tab === 'events' ? 'Event Log' : tab === 'stats' ? 'Stats' : tab === 'costflow' ? 'Cost Flow' : tab === 'canvas' ? 'Canvas' : 'Replay'}
              {tab === 'stats' && resultStats?.total_tokens ? ` (${resultStats.total_tokens.toLocaleString()} tok)` : ''}
            </button>
          ))}
          {resultStats?.elapsed_seconds != null && (
            <span className="ml-auto px-3 py-2 text-[11px] text-text-muted self-center">
              {resultStats.elapsed_seconds}s
            </span>
          )}
        </div>

        {/* Output tab */}
        {resultTab === 'output' && (
          <div className="p-4 max-h-[600px] overflow-auto">
            {outputText ? (
              <>
                <div className="flex gap-1.5 mb-3">
                  <button
                    type="button"
                    onClick={handleCopyOutput}
                    className="flex items-center gap-1 text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                  >
                    <Copy size={12} />
                    Copy
                  </button>
                  <button
                    type="button"
                    onClick={handleDownloadOutput}
                    className="flex items-center gap-1 text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                  >
                    <Download size={12} />
                    Download
                  </button>
                  <ShareButton
                    agentName={run.workflow_name}
                    inputText={inputMessage}
                    outputText={outputText}
                    totalTokens={resultStats?.total_tokens}
                    source="workflow"
                  />
                </div>
                <div className="prose prose-sm dark:prose-invert max-w-none text-text-primary [&_pre]:bg-bg-subtle [&_pre]:p-3 [&_pre]:rounded [&_pre]:border [&_pre]:border-border [&_code]:text-xs [&_code]:font-[family-name:var(--font-mono)] [&_table]:text-xs [&_th]:px-2 [&_td]:px-2">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {outputText}
                  </ReactMarkdown>
                </div>
              </>
            ) : isActive ? (
              <div className="text-sm text-text-muted">Running workflow...</div>
            ) : run.error ? (
              <div className="text-sm text-error whitespace-pre-wrap">{run.error}</div>
            ) : (
              <div className="text-sm text-text-muted">No output recorded.</div>
            )}
          </div>
        )}

        {/* Events tab */}
        {resultTab === 'events' && (
          <div className="bg-bg-subtle p-4 max-h-[600px] overflow-auto font-[family-name:var(--font-mono)] text-xs leading-[1.8]">
            {events.length === 0 ? (
              <div className="text-text-muted text-center py-md">
                {isActive ? 'Waiting for events...' : 'No events recorded.'}
              </div>
            ) : (
              events.map((evt, i) => {
                let parsedData = '';
                try {
                  parsedData = JSON.stringify(evt.data, null, 2);
                } catch {
                  parsedData = String(evt.data);
                }

                return (
                  <div key={i}>
                    <span className="text-[11px] text-text-muted mr-2">
                      {new Date(evt.created_at).toLocaleTimeString()}
                    </span>
                    <span
                      className={
                        evt.event_type === 'workflow_finished' || evt.event_type === 'step_completed'
                          ? 'text-success'
                          : evt.event_type === 'workflow_failed'
                            ? 'text-error'
                            : 'text-info'
                      }
                    >
                      {evt.event_type.replace(/_/g, ' ')}
                    </span>
                    <span className="text-text-muted"> &mdash; </span>
                    <span className="text-text-secondary whitespace-pre-wrap">{parsedData}</span>
                  </div>
                );
              })
            )}
          </div>
        )}

        {/* Stats tab */}
        {resultTab === 'stats' && (
          <div className="p-4 max-h-[600px] overflow-auto">
            {resultStats ? (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-3 gap-3">
                  <div className="bg-bg-subtle rounded-lg p-3 text-center">
                    <div className="text-lg font-semibold text-text-primary">{resultStats.elapsed_seconds ?? '\u2014'}s</div>
                    <div className="text-[11px] text-text-muted">Duration</div>
                  </div>
                  <div className="bg-bg-subtle rounded-lg p-3 text-center">
                    <div className="text-lg font-semibold text-text-primary">{resultStats.total_tokens?.toLocaleString() ?? '\u2014'}</div>
                    <div className="text-[11px] text-text-muted">Total Tokens</div>
                  </div>
                  <div className="bg-bg-subtle rounded-lg p-3 text-center">
                    <div className="text-lg font-semibold text-text-primary">{resultStats.agents?.length ?? 0}</div>
                    <div className="text-[11px] text-text-muted">Agents Used</div>
                  </div>
                </div>

                {resultStats.total_tokens != null && resultStats.total_tokens > 0 && (
                  <div className="text-xs text-text-muted">
                    Input: {resultStats.total_input_tokens?.toLocaleString()} &middot; Output: {resultStats.total_output_tokens?.toLocaleString()}
                  </div>
                )}

                {resultStats.agents && resultStats.agents.length > 0 && (
                  <div className="border border-border rounded-lg overflow-hidden">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="bg-bg-subtle text-text-muted">
                          <th className="text-left px-3 py-2 font-medium">Agent</th>
                          <th className="text-left px-3 py-2 font-medium">Model</th>
                          <th className="text-right px-3 py-2 font-medium">Input</th>
                          <th className="text-right px-3 py-2 font-medium">Output</th>
                          <th className="text-right px-3 py-2 font-medium">Total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {resultStats.agents.map((a) => (
                          <tr key={a.name} className="border-t border-border">
                            <td className="px-3 py-2 font-medium text-text-primary">{a.name}</td>
                            <td className="px-3 py-2 font-[family-name:var(--font-mono)] text-text-muted">{a.model}</td>
                            <td className="px-3 py-2 text-right text-text-muted">{a.input_tokens.toLocaleString()}</td>
                            <td className="px-3 py-2 text-right text-text-muted">{a.output_tokens.toLocaleString()}</td>
                            <td className="px-3 py-2 text-right font-medium">{a.total_tokens.toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-sm text-text-muted">No stats available for this run.</div>
            )}
          </div>
        )}

        {/* Cost Flow tab */}
        {resultTab === 'costflow' && (
          <div className="p-4">
            {resultStats?.agents && resultStats.agents.length > 0 ? (
              <SankeyDiagram agents={resultStats.agents} />
            ) : (
              <div className="text-sm text-text-muted text-center py-8">
                No token data available for cost flow visualization.
              </div>
            )}
          </div>
        )}

        {/* Canvas tab */}
        {resultTab === 'canvas' && (
          <div className="p-4">
            <ExecutionCanvas
              runId={runId}
              workflowDefinition={parsedDef as Record<string, unknown> | null}
              events={events}
              isLive={isActive}
            />
          </div>
        )}

        {/* Replay tab */}
        {resultTab === 'replay' && (
          <div className="p-4">
            {!isActive && events.length > 0 ? (
              <ReplayPlayer
                runId={runId}
                workflowDefinition={parsedDef as Record<string, unknown> | null}
                events={events}
              />
            ) : (
              <div className="text-sm text-text-muted text-center py-8">
                {isActive
                  ? 'Replay is available after the workflow completes.'
                  : 'No events recorded for replay.'}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input section */}
      {inputMessage && (
        <Card className="mb-md">
          <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">Input</h3>
          <pre className="bg-bg-subtle p-md rounded-md text-xs whitespace-pre-wrap font-[family-name:var(--font-mono)] m-0">
            {inputMessage}
          </pre>
        </Card>
      )}

      {/* Replay workflow */}
      {yamlSpec && (
        <Card className="mb-md">
          {!replayOpen ? (
            <button
              type="button"
              onClick={() => { setReplayInput(inputMessage); setReplayOpen(true); }}
              className="flex items-center gap-2 text-sm text-text-muted hover:text-primary border-none bg-transparent cursor-pointer p-0"
            >
              <Play size={14} />
              Re-run this workflow
            </button>
          ) : (
            <div>
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">
                Re-run workflow
              </h3>
              <p className="text-xs text-text-muted mt-0 mb-sm">Same workflow definition — edit the input below or run as-is.</p>
              <textarea
                value={replayInput}
                onChange={(e) => setReplayInput(e.target.value)}
                placeholder="Enter input message..."
                rows={3}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface font-[family-name:var(--font-mono)] resize-y outline-none focus:border-primary"
              />
              <div className="flex gap-2 mt-sm">
                <Button
                  onClick={handleReplay}
                  disabled={replaying || !replayInput.trim()}
                  size="sm"
                >
                  <Play size={13} className="mr-1" />
                  {replaying ? 'Starting...' : 'Run'}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => { setReplayOpen(false); setReplayInput(''); }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </Card>
      )}

      <ConfirmDialog
        open={showCancel}
        onClose={() => setShowCancel(false)}
        onConfirm={handleCancel}
        title="Cancel Workflow"
        message="Are you sure you want to cancel this workflow run? This action cannot be undone."
        confirmLabel="Cancel Run"
      />
    </div>
  );
}
