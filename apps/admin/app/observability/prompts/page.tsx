'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import { playgroundApi } from '@/utils/playground-api';
import type { PromptLogSummary, PromptLogDetail } from '@/utils/types';
import type { ReplayResponse } from '@/utils/types';
import { Card, Badge, Button, Skeleton, EmptyState, useToast } from '@/components/ui/legacy';
import { Copy, Download, Bookmark, BookmarkCheck, Search, X, Trash2 } from 'lucide-react';

export default function PromptHistoryPage() {
  const [logs, setLogs] = useState<PromptLogSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterAgent, setFilterAgent] = useState('');
  const [filterModel, setFilterModel] = useState('');
  const [debouncedAgent, setDebouncedAgent] = useState('');
  const [debouncedModel, setDebouncedModel] = useState('');
  const { toast } = useToast();

  // Pagination
  const [cursor, setCursor] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // Detail/expand
  const [expandedLog, setExpandedLog] = useState<string | null>(null);
  const [detail, setDetail] = useState<PromptLogDetail | null>(null);

  // Replay
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [replayModel, setReplayModel] = useState('gpt-4o-mini');
  const [replaying, setReplaying] = useState(false);
  const [replayResult, setReplayResult] = useState<ReplayResponse | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);

  // Debounce filter inputs
  useEffect(() => {
    const t = setTimeout(() => setDebouncedAgent(filterAgent), 300);
    return () => clearTimeout(t);
  }, [filterAgent]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedModel(filterModel), 300);
    return () => clearTimeout(t);
  }, [filterModel]);

  // Fetch available models on mount
  useEffect(() => {
    playgroundApi.listModels().then((raw: any[]) => {
      // API returns {id, provider, supports_tools} objects — extract id strings
      const ids = raw.map((m: any) => typeof m === 'string' ? m : m.id);
      const models = [...new Set(ids)].filter(Boolean) as string[];
      setAvailableModels(models);
      if (models.length > 0 && !models.includes(replayModel)) {
        setReplayModel(models[0]);
      }
    }).catch(() => {
      // Models API unavailable — replayModel will use log's own model as fallback
      setAvailableModels([]);
    });
  }, []);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const result = await adminApi.listPromptLogs({
        agent_name: debouncedAgent || undefined,
        model: debouncedModel || undefined,
        limit: 50,
      });
      setLogs(result.items);
      setCursor(result.next_cursor ?? undefined);
      setHasMore(result.has_more);
      setError(null);
    } catch {
      setError('Failed to load prompt history.');
    } finally {
      setLoading(false);
    }
  }, [debouncedAgent, debouncedModel]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  async function handleLoadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await adminApi.listPromptLogs({
        agent_name: debouncedAgent || undefined,
        model: debouncedModel || undefined,
        cursor,
        limit: 50,
      });
      setLogs(prev => [...prev, ...page.items]);
      setCursor(page.next_cursor ?? undefined);
      setHasMore(page.has_more);
    } catch {
      setError('Failed to load more.');
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleExpand(logId: string) {
    if (expandedLog === logId) {
      setExpandedLog(null);
      setDetail(null);
      setReplayResult(null);
      setReplayError(null);
      return;
    }
    setExpandedLog(logId);
    setReplayResult(null);
    setReplayError(null);
    // Set replay model to this log's model if no models loaded from API
    const thisLog = logs.find(l => l.log_id === logId);
    if (availableModels.length === 0 && thisLog) {
      setReplayModel(thisLog.model);
    }
    try {
      const data = await adminApi.getPromptLog(logId);
      setDetail(data);
    } catch {
      setError('Failed to load detail.');
    }
  }

  async function handleReplay(logId: string) {
    if (!replayModel) {
      setReplayError('No model selected. Please select a model first.');
      return;
    }
    setReplaying(true);
    setReplayResult(null);
    setReplayError(null);
    try {
      const data = await adminApi.replayPrompt(logId, replayModel);
      setReplayResult(data);
      toast('success', `Replayed with ${data.replay_model}`);
    } catch (err: any) {
      const msg = err?.message || 'Replay failed. Check that the model is available and the backend is running.';
      setReplayError(msg);
      toast('error', 'Replay failed');
    } finally {
      setReplaying(false);
    }
  }

  function handleCopy(text: string) {
    navigator.clipboard.writeText(text);
    toast('success', 'Copied to clipboard');
  }

  function handleDownload(filename: string, content: string) {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    toast('success', 'Downloaded');
  }

  async function toggleExample(log: PromptLogSummary) {
    try {
      await adminApi.updatePromptLog(log.log_id, { is_example: !log.is_example });
      setLogs(prev =>
        prev.map(l => l.log_id === log.log_id ? { ...l, is_example: !l.is_example } : l),
      );
      toast('success', log.is_example ? 'Removed from examples' : 'Marked as example');
    } catch {
      toast('error', 'Failed to update');
    }
  }

  async function handleDelete(logId: string) {
    try {
      await adminApi.deletePromptLog(logId);
      setLogs(prev => prev.filter(l => l.log_id !== logId));
      if (expandedLog === logId) {
        setExpandedLog(null);
        setDetail(null);
      }
      toast('success', 'Prompt deleted');
    } catch {
      toast('error', 'Failed to delete');
    }
  }

  const sourceColors: Record<string, string> = {
    playground: 'info',
    workflow: 'warning',
    api: 'success',
  };

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Prompt History</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Every LLM call is automatically logged here. Mark any prompt as a few-shot example to auto-inject it in Playground runs.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-3 mb-md flex-wrap items-center">
        <div className="relative">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            className="pl-8 pr-7 py-2 border border-border rounded-md text-sm w-[200px] bg-bg-surface outline-none focus:border-primary"
            placeholder="Filter by agent..."
            value={filterAgent}
            onChange={(e) => setFilterAgent(e.target.value)}
          />
          {filterAgent && (
            <button
              type="button"
              onClick={() => setFilterAgent('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary border-none bg-transparent cursor-pointer"
            >
              <X size={12} />
            </button>
          )}
        </div>
        <input
          className="px-3 py-2 border border-border rounded-md text-sm w-[200px] bg-bg-surface"
          placeholder="Filter by model..."
          value={filterModel}
          onChange={(e) => setFilterModel(e.target.value)}
        />
        {!loading && logs.length > 0 && (
          <span className="text-xs text-text-muted bg-bg-surface border border-border rounded-full px-2.5 py-1">
            {logs.length} result{logs.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {loading ? (
        <Card><Skeleton lines={8} /></Card>
      ) : logs.length === 0 ? (
        <Card>
          <EmptyState title="No Prompts" description="No prompt history found. Run agents in the Playground to see LLM calls logged here." />
        </Card>
      ) : (
        <div className="flex flex-col gap-2">
          {logs.map((log) => {
            const isOpen = expandedLog === log.log_id;
            return (
              <Card key={log.log_id} className="!p-0 overflow-hidden">
                <button
                  type="button"
                  onClick={() => handleExpand(log.log_id)}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-left bg-transparent border-none cursor-pointer hover:bg-primary/5 dark:hover:bg-white/[0.03] transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                      <span className="font-semibold text-sm">{log.agent_name}</span>
                      <Badge variant={(sourceColors[log.source] ?? 'info') as 'info' | 'warning' | 'success'}>
                        {log.source}
                      </Badge>
                      {log.is_example && <Badge variant="success">example</Badge>}
                      {log.tags.map((tag) => (
                        <Badge key={tag} variant="info">{tag}</Badge>
                      ))}
                      <span className="text-[11px] text-text-muted font-[family-name:var(--font-mono)]">{log.model}</span>
                      {log.step_index > 0 && <span className="text-[11px] text-text-muted">step {log.step_index}</span>}
                    </div>
                    {log.input_text ? (
                      <div className="text-xs text-text-muted truncate">
                        {log.input_text.slice(0, 120)}{log.input_text.length > 120 ? '...' : ''}
                      </div>
                    ) : null}
                  </div>
                  <div className="text-xs text-text-muted shrink-0 text-right">
                    <div>{(log.input_tokens + log.output_tokens).toLocaleString()} tok · ${log.cost_usd.toFixed(4)}</div>
                    <div>{log.duration_ms}ms · {new Date(log.created_at * 1000).toLocaleString()}</div>
                  </div>
                </button>

                {isOpen && detail && (
                  <div className="border-t border-border px-4 py-3">
                    {/* Action buttons */}
                    <div className="flex gap-1.5 mb-md flex-wrap">
                      <button
                        type="button"
                        onClick={() => {
                          const full = detail.prompt_messages.map(m => `[${m.role}]\n${m.content}`).join('\n\n') + '\n\n[Response]\n' + detail.response_message.content;
                          handleCopy(full);
                        }}
                        className="flex items-center gap-1 text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                      >
                        <Copy size={12} /> Copy
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const full = `# Prompt — ${log.agent_name}\n\n**Model:** ${log.model}\n**Tokens:** ${log.input_tokens + log.output_tokens}\n**Cost:** $${log.cost_usd.toFixed(4)}\n\n` +
                            detail.prompt_messages.map(m => `## ${m.role}\n\n${m.content}`).join('\n\n') + `\n\n## Response\n\n${detail.response_message.content}`;
                          handleDownload(`prompt-${log.log_id}.md`, full);
                        }}
                        className="flex items-center gap-1 text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                      >
                        <Download size={12} /> Download
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleExample(log)}
                        className="flex items-center gap-1 text-[11px] text-text-muted hover:text-primary border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                      >
                        {log.is_example ? (
                          <><BookmarkCheck size={12} /> Remove example</>
                        ) : (
                          <><Bookmark size={12} /> Mark as example</>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(log.log_id)}
                        className="flex items-center gap-1 text-[11px] text-text-muted hover:text-error border border-border rounded px-2 py-1 bg-transparent cursor-pointer"
                      >
                        <Trash2 size={12} /> Delete
                      </button>
                    </div>

                    {/* Input/Output text (if available from manual save) */}
                    {log.input_text && (
                      <div className="mb-3">
                        <h4 className="text-xs font-semibold text-text-muted uppercase mb-1">Input</h4>
                        <pre className="bg-bg-subtle p-3 rounded-md text-xs whitespace-pre-wrap font-[family-name:var(--font-mono)] m-0 max-h-[200px] overflow-auto">
                          {log.input_text}
                        </pre>
                      </div>
                    )}
                    {log.output_text && (
                      <div className="mb-3">
                        <h4 className="text-xs font-semibold text-text-muted uppercase mb-1">Output</h4>
                        <pre className="bg-bg-subtle p-3 rounded-md text-xs whitespace-pre-wrap font-[family-name:var(--font-mono)] m-0 max-h-[300px] overflow-auto">
                          {log.output_text}
                        </pre>
                      </div>
                    )}

                    {/* Prompt messages */}
                    <div className="mb-md">
                      <h4 className="mt-0 mb-2 text-sm font-semibold">Prompt Messages</h4>
                      {detail.prompt_messages.map((msg, i) => (
                        <div key={i} className="mb-2 p-3 bg-bg-surface rounded border border-border">
                          <div className="text-[11px] font-semibold text-primary mb-1 uppercase">{msg.role}</div>
                          <div className="text-[13px] whitespace-pre-wrap break-words">{msg.content}</div>
                        </div>
                      ))}
                    </div>

                    {/* Response */}
                    <div className="mb-md">
                      <h4 className="mt-0 mb-2 text-sm font-semibold">Response</h4>
                      <div className="p-3 bg-success-light rounded border border-success/20">
                        <div className="text-[13px] whitespace-pre-wrap break-words">{detail.response_message.content}</div>
                      </div>
                    </div>

                    {/* Metadata */}
                    <div className="flex flex-wrap gap-3 text-xs text-text-muted mb-md">
                      <span>In: {log.input_tokens.toLocaleString()} tok</span>
                      <span>Out: {log.output_tokens.toLocaleString()} tok</span>
                      <span>Strategy: {log.strategy}</span>
                      {log.run_id && <span>Run: {log.run_id.slice(0, 12)}...</span>}
                    </div>

                    {/* Replay */}
                    <div className="flex gap-3 items-end">
                      <div>
                        <label className="block text-xs text-text-muted mb-1">Replay with model</label>
                        <select
                          className="px-3 py-2 border border-border rounded-md text-[13px] w-[200px] bg-bg-surface"
                          value={replayModel}
                          onChange={(e) => setReplayModel(e.target.value)}
                        >
                          {availableModels.length > 0 ? (
                            availableModels.map((m, i) => (
                              <option key={`${m}-${i}`} value={m}>{m}</option>
                            ))
                          ) : (
                            <option value={log.model}>{log.model} (current)</option>
                          )}
                        </select>
                      </div>
                      <Button
                        onClick={() => handleReplay(log.log_id)}
                        disabled={replaying || !replayModel}
                      >
                        {replaying ? 'Replaying...' : 'Replay'}
                      </Button>
                    </div>

                    {replayError && (
                      <div className="mt-2 bg-error-light border border-error/20 rounded px-3 py-2 text-error text-xs">
                        {replayError}
                      </div>
                    )}

                    {replayResult && (
                      <div className="mt-3">
                        <h4 className="mt-0 mb-2 text-sm font-semibold">Replay Result ({replayResult.replay_model})</h4>
                        <div className="p-3 bg-info-light rounded border border-info/20">
                          <div className="text-[13px] whitespace-pre-wrap break-words">{replayResult.replay_response.content}</div>
                        </div>
                        <div className="flex gap-3 text-xs text-text-muted mt-2">
                          <span>Model: {replayResult.replay_model}</span>
                          <button
                            type="button"
                            onClick={() => handleCopy(replayResult.replay_response.content)}
                            className="text-[11px] text-text-muted hover:text-primary border-none bg-transparent cursor-pointer underline"
                          >
                            Copy response
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {!loading && logs.length > 0 && (
        <div className="flex items-center justify-between mt-md">
          <span className="text-xs text-text-muted">
            Showing {logs.length} prompt{logs.length !== 1 ? 's' : ''}{hasMore ? ' (more available)' : ''}
          </span>
          {hasMore && (
            <Button variant="secondary" onClick={handleLoadMore} disabled={loadingMore}>
              {loadingMore ? 'Loading...' : 'Load more'}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
