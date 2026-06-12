'use client';

import { adminApi } from '@/utils/api';
import type { RunDetail } from '@/utils/types';
import Link from 'next/link';
import { useEffect, useState, useCallback, use } from 'react';
import { Card, Badge, Button, Tabs, Skeleton, ConfirmDialog, useToast } from '@/components/ui/legacy';
import { ShareButton } from '@/components/share-button';
import { DirectiveChainPanel } from '@/components/directive-chain-panel';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Props {
  params: Promise<{ id: string }>;
}

export default function RunDetailPage({ params }: Props) {
  const { id } = use(params);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionPending, setActionPending] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');
  const [showCancel, setShowCancel] = useState(false);
  const { toast } = useToast();

  async function fetchRun() {
    try {
      const data = await adminApi.getRun(id);
      setRun(data);
    } catch {
      setRun(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchRun(); }, [id]);

  async function handleCancel() {
    setActionPending(true);
    try {
      await adminApi.cancelRun(id);
      toast('success', 'Run cancelled');
      await fetchRun();
    } catch {
      toast('error', 'Failed to cancel run');
    } finally {
      setActionPending(false);
      setShowCancel(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto">
        <Skeleton lines={1} className="w-32 mb-md" />
        <Skeleton lines={1} className="w-64 mb-lg" />
        <div className="grid grid-cols-4 gap-md"><Skeleton lines={2} /><Skeleton lines={2} /><Skeleton lines={2} /><Skeleton lines={2} /></div>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="max-w-4xl mx-auto">
        <Link href="/runs" className="text-primary no-underline text-sm">&larr; Back to runs</Link>
        <h1 className="mt-md text-xl font-bold font-[family-name:var(--font-heading)]">Run not found</h1>
      </div>
    );
  }

  const duration =
    run.started_at && run.completed_at
      ? ((run.completed_at - run.started_at) * 1000).toFixed(0)
      : null;

  const isActive = run.status === 'running' || run.status === 'paused';

  const statusVariant = run.status === 'completed' ? 'success' : run.status === 'failed' ? 'error' : run.status === 'running' ? 'info' : 'warning';

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'steps', label: `Steps (${run.steps.length})` },
    { id: 'tools', label: `Tools (${run.tool_calls.length})` },
    { id: 'directives', label: 'Directives' },
  ];

  return (
    <div className="max-w-4xl mx-auto">
      <Link href="/runs" className="text-primary no-underline text-sm">&larr; Back to runs</Link>
      <div className="flex items-center gap-md mt-md mb-lg">
        <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Run {run.run_id.slice(0, 12)}...
        </h1>
        <Badge variant={statusVariant}>{run.status}</Badge>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-md mb-lg">
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Agent</div>
          <div className="text-sm font-semibold mt-1">{run.agent_name}</div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Tokens</div>
          <div className="text-sm font-semibold mt-1">{run.total_tokens.toLocaleString()}</div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Duration</div>
          <div className="text-sm font-semibold mt-1">{duration ? `${duration}ms` : '\u2014'}</div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Steps</div>
          <div className="text-sm font-semibold mt-1">{run.steps.length}</div>
        </Card>
      </div>

      {/* Run controls — pause/resume are not implemented (no backend route);
          only cancel is offered for an in-flight run. */}
      {isActive && (
        <div className="flex gap-sm mb-lg">
          <Button variant="danger" size="sm" onClick={() => setShowCancel(true)} disabled={actionPending}>
            Cancel
          </Button>
        </div>
      )}

      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />

      {activeTab === 'overview' && (
        <>
          {run.input_text && (
            <Card className="mb-md">
              <h3 className="mt-0 mb-sm text-sm font-semibold text-text-muted uppercase">Input</h3>
              <pre className="bg-bg-subtle p-md rounded-md text-xs whitespace-pre-wrap font-[family-name:var(--font-mono)] m-0">
                {run.input_text}
              </pre>
            </Card>
          )}
          {run.output_text && (
            <Card>
              <div className="flex items-center justify-between mb-sm">
                <h3 className="mt-0 mb-0 text-sm font-semibold text-text-muted uppercase">Output</h3>
                <div className="flex gap-1.5">
                  <ShareButton
                    agentName={run.agent_name}
                    inputText={run.input_text}
                    outputText={run.output_text}
                    totalTokens={run.total_tokens}
                    source="api"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard.writeText(run.output_text);
                      toast('success', 'Copied to clipboard');
                    }}
                    className="text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                  >
                    Copy
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const blob = new Blob([run.output_text], { type: 'text/markdown' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `run-${run.run_id.slice(0, 8)}-output.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                  >
                    Download
                  </button>
                </div>
              </div>
              <div className="bg-bg-subtle p-md rounded-md prose prose-sm prose-invert max-w-none text-text-primary [&_pre]:bg-[#111827] [&_pre]:p-3 [&_pre]:rounded [&_code]:text-xs [&_code]:font-[family-name:var(--font-mono)] [&_table]:text-xs [&_th]:px-2 [&_td]:px-2">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {run.output_text}
                </ReactMarkdown>
              </div>
            </Card>
          )}
        </>
      )}

      {activeTab === 'steps' && (
        <div className="flex flex-col gap-sm">
          {run.steps.map((step, i) => (
            <Card key={i} className="!p-md">
              <div className="flex items-center gap-sm">
                <Badge variant="info">{step.step_type}</Badge>
                <span className="text-xs text-text-muted font-[family-name:var(--font-mono)]">{step.duration_ms}ms</span>
              </div>
              {step.detail && <p className="text-sm text-text-secondary mt-sm mb-0">{step.detail}</p>}
            </Card>
          ))}
          {run.steps.length === 0 && (
            <p className="text-sm text-text-muted text-center py-lg">No steps recorded.</p>
          )}
        </div>
      )}

      {activeTab === 'tools' && (
        <div className="flex flex-col gap-sm">
          {run.tool_calls.map((tc, i) => (
            <Card key={i} className="!p-md">
              <div className="flex items-center gap-sm mb-sm">
                <span className="font-semibold text-sm">{tc.tool_name}</span>
                <span className="text-xs text-text-muted font-[family-name:var(--font-mono)]">{tc.duration_ms}ms</span>
              </div>
              {tc.arguments && (
                <pre className="bg-bg-subtle p-sm rounded-md text-xs font-[family-name:var(--font-mono)] m-0 mb-sm whitespace-pre-wrap">{tc.arguments}</pre>
              )}
              {tc.result_preview && (
                <p className="text-xs text-success m-0">{tc.result_preview}</p>
              )}
            </Card>
          ))}
          {run.tool_calls.length === 0 && (
            <p className="text-sm text-text-muted text-center py-lg">No tool calls recorded.</p>
          )}
        </div>
      )}

      {activeTab === 'directives' && (
        <Card className="!p-md">
          <DirectiveChainPanel runId={run.run_id} />
        </Card>
      )}

      <ConfirmDialog
        open={showCancel}
        onClose={() => setShowCancel(false)}
        onConfirm={handleCancel}
        title="Cancel Run"
        message="Are you sure you want to cancel this run? This action cannot be undone."
        confirmLabel="Cancel Run"
      />
    </div>
  );
}
