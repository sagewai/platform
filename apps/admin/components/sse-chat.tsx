'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { readSSE } from '@/utils/playground-api';
import { adminApi } from '@/utils/api';
import { authFetch } from '@/utils/auth';
import type { PromptLogSummary } from '@/utils/types';
import { Card, Button } from '@/components/ui/legacy';
import { Copy, Check, ChevronDown, ChevronRight, Code2, ClipboardCopy, Search, BookOpen } from 'lucide-react';
import { ShareButton } from './share-button';

interface AgentLog {
  event: string;
  data: string;
}

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  logs?: AgentLog[];
}

interface Props {
  agentName: string | null;
  onSaveAs?: () => void;
  onExportCode?: () => void;
}

const BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace('/admin', '') ??
  'http://localhost:8000';

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className="absolute top-2 right-2 p-1 rounded bg-white/10 hover:bg-white/20 text-white/60 hover:text-white transition-colors border-none cursor-pointer"
      title="Copy code"
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}

/** Colorize event names for readability */
function eventColor(event: string): string {
  if (event.startsWith('step_')) return 'text-info';
  if (event.includes('tool_call')) return 'text-warning';
  if (event.includes('llm_call')) return 'text-success';
  if (event.includes('error')) return 'text-error';
  return 'text-text-muted';
}

/** Format a log entry: parse JSON for nicer display */
function formatLogData(event: string, raw: string): string {
  try {
    const data = JSON.parse(raw);
    // Show key fields depending on event type
    if (event === 'llm_call_finished') {
      return `model=${data.model} tokens=${data.input_tokens}→${data.output_tokens}`;
    }
    if (event === 'tool_call_start') {
      const args = typeof data.arguments === 'object'
        ? JSON.stringify(data.arguments).slice(0, 80)
        : String(data.arguments ?? '').slice(0, 80);
      return `${data.tool_name}(${args})`;
    }
    if (event === 'tool_call_result') {
      const content = (data.content || data.error || '').slice(0, 100);
      return data.error ? `ERROR: ${content}` : content || '(empty)';
    }
    if (event === 'step_started' || event === 'step_finished') {
      return data.step || raw.slice(0, 100);
    }
  } catch {
    // fall through
  }
  return raw.slice(0, 120) + (raw.length > 120 ? '…' : '');
}

/** Format logs as human-readable text for copying / analysis */
function formatLogsAsText(logs: AgentLog[]): string {
  const lines: string[] = [];
  for (const evt of logs) {
    if (evt.event === 'prompt_logged') continue;
    lines.push(`[${evt.event}] ${formatLogData(evt.event, evt.data)}`);
  }
  return lines.join('\n');
}

/** Build a structured analysis prompt from logs */
function buildAnalysisPrompt(logs: AgentLog[]): string {
  const iterations = logs.filter((l) => l.event === 'step_started').length;
  const toolCalls = logs.filter((l) => l.event === 'tool_call_start').length;
  const errors = logs.filter(
    (l) => l.event === 'tool_call_result' && l.data.includes('"error"'),
  ).length;

  // Extract model info
  const llmEvents = logs.filter((l) => l.event === 'llm_call_finished');
  let modelInfo = '';
  if (llmEvents.length > 0) {
    try {
      const data = JSON.parse(llmEvents[0].data);
      modelInfo = data.model || '';
    } catch { /* ignore */ }
  }

  const logText = formatLogsAsText(logs);

  return [
    `Analyze these agent execution logs and provide insights:`,
    ``,
    `**Run summary**: ${iterations} steps, ${toolCalls} tool calls, ${errors} errors${modelInfo ? `, model: ${modelInfo}` : ''}`,
    ``,
    '```',
    logText,
    '```',
    ``,
    `Please analyze:`,
    `1. What the agent did and whether it was efficient`,
    `2. Any errors — root causes and how to prevent them`,
    `3. Tool usage patterns — any unnecessary or hallucinated calls`,
    `4. Suggestions to improve the agent config or prompt for this task`,
  ].join('\n');
}

function AgentLogs({ logs, isLive, onAnalyze }: { logs: AgentLog[]; isLive?: boolean; onAnalyze?: (prompt: string) => void }) {
  const [open, setOpen] = useState(isLive ?? false);
  const [copied, setCopied] = useState(false);

  // Auto-open while live
  useEffect(() => {
    if (isLive) setOpen(true);
  }, [isLive]);

  if (logs.length === 0) return null;

  const iterations = logs.filter((l) => l.event === 'step_started').length;
  const toolCalls = logs.filter((l) => l.event === 'tool_call_start').length;
  const errors = logs.filter(
    (l) => l.event === 'tool_call_result' && l.data.includes('"error"'),
  ).length;

  function handleCopy() {
    navigator.clipboard.writeText(formatLogsAsText(logs));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleAnalyze() {
    if (onAnalyze) {
      onAnalyze(buildAnalysisPrompt(logs));
    }
  }

  return (
    <div className="mt-1.5 rounded-md border border-border/50 overflow-hidden text-[11px]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-1.5 px-2.5 py-1.5 bg-bg-subtle/50 hover:bg-bg-subtle border-none cursor-pointer text-text-muted transition-colors text-left"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-medium">Agent Trace</span>
        <span className="ml-auto flex gap-2 text-[10px]">
          {isLive && (
            <span className="text-success animate-pulse">● running</span>
          )}
          <span>{iterations} step{iterations !== 1 ? 's' : ''}</span>
          {toolCalls > 0 && <span>{toolCalls} tool call{toolCalls !== 1 ? 's' : ''}</span>}
          {errors > 0 && <span className="text-error">{errors} error{errors !== 1 ? 's' : ''}</span>}
        </span>
      </button>
      {open && (
        <>
          <div className="px-2.5 py-2 max-h-[300px] overflow-auto font-[family-name:var(--font-mono)] leading-relaxed bg-[#0d1117] text-[#c9d1d9]">
            {logs
              .filter((evt) => evt.event !== 'prompt_logged') // skip verbose prompt dumps
              .map((evt, i) => (
              <div key={i} className="flex gap-2 py-0.5">
                <span className={`shrink-0 ${eventColor(evt.event)}`}>
                  {evt.event}
                </span>
                <span className="text-[#8b949e] truncate">
                  {formatLogData(evt.event, evt.data)}
                </span>
              </div>
            ))}
          </div>
          {!isLive && (
            <div className="flex gap-1.5 px-2.5 py-1.5 border-t border-border/30 bg-bg-subtle/30">
              <button
                type="button"
                onClick={handleCopy}
                className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-transparent hover:bg-white/10 border border-border/50 cursor-pointer text-text-muted transition-colors"
                title="Copy logs to clipboard"
              >
                {copied ? <Check size={10} /> : <ClipboardCopy size={10} />}
                {copied ? 'Copied' : 'Copy Logs'}
              </button>
              {onAnalyze && (
                <button
                  type="button"
                  onClick={handleAnalyze}
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-transparent hover:bg-primary/10 hover:text-primary border border-border/50 cursor-pointer text-text-muted transition-colors"
                  title="Send logs to the agent for analysis"
                >
                  <Search size={10} />
                  Analyze Logs
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || '');
          const codeString = String(children).replace(/\n$/, '');
          if (match) {
            return (
              <div className="relative my-2 rounded-lg overflow-hidden">
                <div className="flex items-center justify-between px-3 py-1.5 bg-[#1e1e2e] text-[11px] text-white/50 font-[family-name:var(--font-mono)]">
                  {match[1]}
                </div>
                <CopyButton text={codeString} />
                <SyntaxHighlighter
                  style={oneDark}
                  language={match[1]}
                  PreTag="div"
                  customStyle={{
                    margin: 0,
                    borderRadius: 0,
                    fontSize: '12.5px',
                    padding: '12px 16px',
                  }}
                >
                  {codeString}
                </SyntaxHighlighter>
              </div>
            );
          }
          return (
            <code
              className="px-1.5 py-0.5 rounded bg-black/10 text-[12.5px] font-[family-name:var(--font-mono)]"
              {...props}
            >
              {children}
            </code>
          );
        },
        p({ children }) {
          return <p className="m-0 mb-2 last:mb-0">{children}</p>;
        },
        ul({ children }) {
          return <ul className="m-0 mb-2 pl-5 last:mb-0">{children}</ul>;
        },
        ol({ children }) {
          return <ol className="m-0 mb-2 pl-5 last:mb-0">{children}</ol>;
        },
        li({ children }) {
          return <li className="mb-0.5">{children}</li>;
        },
        h1({ children }) {
          return <h1 className="text-base font-bold mt-3 mb-1 first:mt-0">{children}</h1>;
        },
        h2({ children }) {
          return <h2 className="text-[15px] font-bold mt-3 mb-1 first:mt-0">{children}</h2>;
        },
        h3({ children }) {
          return <h3 className="text-sm font-bold mt-2 mb-1 first:mt-0">{children}</h3>;
        },
        blockquote({ children }) {
          return (
            <blockquote className="border-l-3 border-primary/40 pl-3 my-2 text-text-muted italic">
              {children}
            </blockquote>
          );
        },
        table({ children }) {
          return (
            <div className="overflow-x-auto my-2">
              <table className="w-full text-[12.5px] border-collapse">{children}</table>
            </div>
          );
        },
        th({ children }) {
          return (
            <th className="border border-border px-2 py-1 bg-bg-subtle text-left font-semibold">
              {children}
            </th>
          );
        },
        td({ children }) {
          return <td className="border border-border px-2 py-1">{children}</td>;
        },
        hr() {
          return <hr className="border-border my-3" />;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

export function SSEChat({ agentName, onSaveAs, onExportCode }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [running, setRunning] = useState(false);
  const [liveEvents, setLiveEvents] = useState<AgentLog[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Few-shot examples
  const [examples, setExamples] = useState<PromptLogSummary[]>([]);
  const [enabledExamples, setEnabledExamples] = useState<Set<string>>(new Set());
  const [examplesOpen, setExamplesOpen] = useState(false);

  // Load examples when agent changes
  useEffect(() => {
    if (!agentName) {
      setExamples([]);
      setEnabledExamples(new Set());
      return;
    }
    adminApi
      .listExamples(agentName, 50)
      .then((exs) => {
        setExamples(exs);
        // Auto-enable all examples by default
        setEnabledExamples(new Set(exs.map((e) => e.log_id)));
        if (exs.length > 0) setExamplesOpen(true);
      })
      .catch(() => setExamples([]));
  }, [agentName]);

  function toggleExample(id: string) {
    setEnabledExamples((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Auto-focus input when agent becomes active
  useEffect(() => {
    if (agentName) {
      inputRef.current?.focus();
    }
  }, [agentName]);

  // Re-focus input after response completes
  useEffect(() => {
    if (!running && agentName) {
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [running, agentName]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, liveEvents]);

  // Auto-resize textarea as content grows
  function autoResize(el: HTMLTextAreaElement) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }

  const sendMessage = useCallback(async (messageOverride?: string) => {
    const text = messageOverride ?? input;
    if (!agentName || !text.trim() || running) return;

    const userMsg = text.trim();
    setInput('');
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
    }
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }]);
    setLiveEvents([]);
    setRunning(true);

    let streamingStarted = false;
    // Collect all events for this run to attach as logs
    const runLogs: AgentLog[] = [];

    try {
      // Build few-shot examples payload
      const activeExamples = examples
        .filter((e) => enabledExamples.has(e.log_id))
        .map((e) => ({ input: e.input_text, output: e.output_text }));

      const resp = await authFetch(`${BASE}/playground/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_name: agentName,
          message: userMsg,
          ...(activeExamples.length > 0 ? { examples: activeExamples } : {}),
        }),
      });

      if (!resp.ok) {
        const err = await resp.text();
        setMessages((prev) => [
          ...prev,
          { role: 'system', content: `Error: ${err}` },
        ]);
        setRunning(false);
        return;
      }

      for await (const evt of readSSE(resp)) {
        console.log('[SSE]', evt.event, evt.data.slice(0, 200));
        runLogs.push(evt);
        setLiveEvents([...runLogs]);

        if (evt.event === 'text_message_content') {
          try {
            const data = JSON.parse(evt.data);
            const delta = data.delta ?? '';
            if (delta) {
              if (!streamingStarted) {
                streamingStarted = true;
                setMessages((prev) => [
                  ...prev,
                  { role: 'assistant', content: delta },
                ]);
              } else {
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = {
                      ...last,
                      content: last.content + delta,
                    };
                  }
                  return updated;
                });
              }
            }
          } catch {
            // ignore parse error
          }
        } else if (evt.event === 'run_finished') {
          try {
            const data = JSON.parse(evt.data);
            const output = data.output ?? '';
            if (streamingStarted) {
              if (output) {
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = { ...last, content: output, logs: runLogs };
                  }
                  return updated;
                });
              } else {
                // Attach logs to the existing message
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  if (last?.role === 'assistant') {
                    updated[updated.length - 1] = { ...last, logs: runLogs };
                  }
                  return updated;
                });
              }
            } else if (output) {
              setMessages((prev) => [
                ...prev,
                { role: 'assistant', content: output, logs: runLogs },
              ]);
            } else {
              // No output at all — show logs as a system message
              setMessages((prev) => [
                ...prev,
                { role: 'assistant', content: '[No response generated]', logs: runLogs },
              ]);
            }
          } catch {
            // ignore parse error
          }
          // Run is done — clear running state immediately rather than
          // waiting for the SSE stream to close (keepalive pings can
          // keep the connection open indefinitely).
          setLiveEvents([]);
          setRunning(false);
          break;
        } else if (evt.event === 'run_error') {
          try {
            const data = JSON.parse(evt.data);
            setMessages((prev) => [
              ...prev,
              { role: 'system', content: `Error: ${data.error}`, logs: runLogs },
            ]);
          } catch {
            // ignore
          }
          setLiveEvents([]);
          setRunning(false);
          break;
        }
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: 'system', content: `Connection error: ${String(e)}`, logs: runLogs },
      ]);
    } finally {
      setLiveEvents([]);
      setRunning(false);
    }
  }, [agentName, input, running, examples, enabledExamples]);

  const send = useCallback(() => sendMessage(), [sendMessage]);

  const handleAnalyzeLogs = useCallback(
    (prompt: string) => sendMessage(prompt),
    [sendMessage],
  );

  if (!agentName) {
    return (
      <Card className="h-full flex items-center justify-center text-text-muted text-sm overflow-hidden">
        Create an agent to start chatting.
      </Card>
    );
  }

  return (
    <div className="bg-bg-surface rounded-lg border border-border flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border font-semibold text-sm text-text-secondary flex items-center gap-2">
        <div
          className={`w-2 h-2 rounded-full ${running ? 'bg-success animate-pulse' : 'bg-text-muted'}`}
        />
        <span className="flex-1">Chat with {agentName}</span>
        {onExportCode && (
          <button
            type="button"
            onClick={onExportCode}
            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium bg-bg-subtle hover:bg-primary/10 hover:text-primary border border-border cursor-pointer text-text-muted transition-colors"
            title="Export agent as Python code"
          >
            <Code2 size={12} />
            Export Code
          </button>
        )}
        {onSaveAs && (
          <button
            type="button"
            onClick={onSaveAs}
            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium bg-bg-subtle hover:bg-primary/10 hover:text-primary border border-border cursor-pointer text-text-muted transition-colors"
            title="Clone this agent to the registry with a different name or model"
          >
            <Copy size={12} />
            Clone Agent
          </button>
        )}
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-auto p-4 flex flex-col gap-3"
      >
        {messages.length === 0 && (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            Send a message to start the conversation.
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-xl text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'self-end bg-primary text-white px-3.5 py-2.5 whitespace-pre-wrap'
                : msg.role === 'system'
                  ? 'self-start bg-error-light text-error px-3.5 py-2.5 whitespace-pre-wrap'
                  : 'self-start bg-bg-subtle px-4 py-3'
            }`}
          >
            {msg.role === 'assistant' ? (
              <>
                <MarkdownContent content={msg.content} />
                {msg.logs && msg.logs.length > 0 && (
                  <AgentLogs logs={msg.logs} onAnalyze={handleAnalyzeLogs} />
                )}
                {!running && agentName && i > 0 && (
                  <div className="mt-2 flex justify-end">
                    <ShareButton
                      agentName={agentName}
                      inputText={messages[i - 1]?.content ?? ''}
                      outputText={msg.content}
                      source="playground"
                    />
                  </div>
                )}
              </>
            ) : (
              <>
                {msg.content}
                {msg.logs && msg.logs.length > 0 && (
                  <AgentLogs logs={msg.logs} onAnalyze={handleAnalyzeLogs} />
                )}
              </>
            )}
          </div>
        ))}

        {/* Live events while running (before response is finalized) */}
        {running && liveEvents.length > 0 && (
          <div className="self-start max-w-[85%]">
            <AgentLogs logs={liveEvents} isLive />
          </div>
        )}
      </div>

      {/* Few-shot examples panel */}
      {examples.length > 0 && (
        <div className="border-t border-border">
          <button
            type="button"
            onClick={() => setExamplesOpen(!examplesOpen)}
            className="w-full flex items-center gap-1.5 px-3 py-2 bg-transparent border-none cursor-pointer text-[12px] text-text-muted hover:text-text-secondary transition-colors text-left"
          >
            {examplesOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <BookOpen size={12} />
            <span className="font-medium">Few-shot Examples</span>
            <span className="ml-auto text-[11px]">
              {enabledExamples.size}/{examples.length} active
            </span>
          </button>
          {examplesOpen && (
            <div className="px-3 pb-2 flex flex-col gap-1.5 max-h-[200px] overflow-auto">
              {examples.map((ex) => {
                const enabled = enabledExamples.has(ex.log_id);
                return (
                  <label
                    key={ex.log_id}
                    className={`flex items-start gap-2 p-2 rounded-md cursor-pointer text-[11px] transition-colors ${
                      enabled
                        ? 'bg-primary/8 border border-primary/25'
                        : 'bg-bg-subtle/50 border border-transparent opacity-60'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={enabled}
                      onChange={() => toggleExample(ex.log_id)}
                      className="mt-0.5 accent-primary shrink-0"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-text-secondary font-medium truncate">
                        Q: {ex.input_text.slice(0, 80)}{ex.input_text.length > 80 ? '…' : ''}
                      </div>
                      <div className="text-text-muted truncate mt-0.5">
                        A: {ex.output_text.slice(0, 80)}{ex.output_text.length > 80 ? '…' : ''}
                      </div>
                    </div>
                  </label>
                );
              })}
              <div className="flex gap-2 mt-1">
                <button
                  type="button"
                  onClick={() => setEnabledExamples(new Set(examples.map((e) => e.log_id)))}
                  className="text-[10px] text-primary hover:underline bg-transparent border-none cursor-pointer p-0"
                >
                  Enable all
                </button>
                <button
                  type="button"
                  onClick={() => setEnabledExamples(new Set())}
                  className="text-[10px] text-text-muted hover:underline bg-transparent border-none cursor-pointer p-0"
                >
                  Disable all
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Input — multi-line textarea, Enter sends, Shift+Enter for newline */}
      <div className="p-3 border-t border-border flex gap-2 items-end">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            autoResize(e.target);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Type a message... (Shift+Enter for new line)"
          disabled={running}
          rows={1}
          className="flex-1 px-3 py-[9px] rounded-md border border-border text-sm bg-bg-surface outline-none focus:border-primary transition-colors resize-none font-[inherit] leading-relaxed"
        />
        <Button onClick={send} disabled={running || !input.trim()}>
          {running ? 'Running...' : 'Send'}
        </Button>
      </div>
    </div>
  );
}
