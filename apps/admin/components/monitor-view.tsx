'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { AGUIEvent, RunTimeline } from '@/utils/agui-types';
import { EventTimeline } from './event-timeline';
import { Button } from '@sagecurator/ui';
import { authSSE } from '@/utils/auth';

const SSE_URL = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? `${process.env.NEXT_PUBLIC_ADMIN_API_URL}/events/stream`
  : 'http://localhost:8000/admin/events/stream';

function generateDemoEvents(): AGUIEvent[] {
  const now = new Date();
  const ts = (offset: number) => new Date(now.getTime() + offset).toISOString();
  const runId = `run_demo_${Date.now().toString(36)}`;

  return [
    { type: 'run_started', timestamp: ts(0), run_id: runId, agent_name: 'scout', data: { input: 'Find information about quantum computing' } },
    { type: 'text_message_start', timestamp: ts(100), run_id: runId, data: {} },
    { type: 'text_message_content', timestamp: ts(200), run_id: runId, data: { delta: 'Let me search for ' } },
    { type: 'text_message_content', timestamp: ts(350), run_id: runId, data: { delta: 'information about quantum computing...' } },
    { type: 'text_message_end', timestamp: ts(500), run_id: runId, data: {} },
    { type: 'tool_call_start', timestamp: ts(600), run_id: runId, data: { tool_name: 'web_search', arguments: { query: 'quantum computing overview 2026' } } },
    { type: 'tool_call_end', timestamp: ts(1800), run_id: runId, data: { tool_name: 'web_search', result: '5 results found', duration_ms: 1200 } },
    { type: 'state_delta', timestamp: ts(1900), run_id: runId, data: { search_results: 5, tokens_used: 320 } },
    { type: 'text_message_start', timestamp: ts(2000), run_id: runId, data: {} },
    { type: 'text_message_content', timestamp: ts(2100), run_id: runId, data: { delta: 'Based on my research, quantum computing...' } },
    { type: 'text_message_end', timestamp: ts(2300), run_id: runId, data: {} },
    { type: 'run_finished', timestamp: ts(2400), run_id: runId, data: { output: 'Quantum computing summary...', total_tokens: 450 } },
  ];
}

export function MonitorView() {
  const [connected, setConnected] = useState(false);
  const [runs, setRuns] = useState<Map<string, RunTimeline>>(new Map());
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const addEvent = useCallback((event: AGUIEvent) => {
    if (!event.run_id) return; // skip heartbeat/control events
    setRuns((prev) => {
      const next = new Map(prev);
      const existing = next.get(event.run_id);
      if (existing) {
        existing.events.push(event);
        if (event.type === 'run_finished') {
          existing.status = 'completed';
          existing.finished_at = event.timestamp;
        } else if (event.type === 'run_error') {
          existing.status = 'error';
          existing.finished_at = event.timestamp;
        }
      } else {
        next.set(event.run_id, {
          run_id: event.run_id,
          agent_name: event.agent_name ?? 'unknown',
          status: 'running',
          events: [event],
          started_at: event.timestamp,
        });
        setSelectedRunId(event.run_id);
      }
      return next;
    });
  }, []);

  const connect = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const url = agentFilter ? `${SSE_URL}?agent_name=${encodeURIComponent(agentFilter)}` : SSE_URL;
    setConnected(true);
    const controller = authSSE(
      url,
      (_event, data) => {
        try {
          addEvent(data as unknown as AGUIEvent);
        } catch {
          // ignore parse errors
        }
      },
      {
        reconnect: true,
        onError: () => {
          setConnected(false);
          abortRef.current = null;
        },
      },
    );
    abortRef.current = controller;
  }, [addEvent, agentFilter]);

  const disconnect = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setConnected(false);
  }, []);

  const startDemo = useCallback(() => {
    const events = generateDemoEvents();
    let i = 0;
    const interval = setInterval(() => {
      if (i >= events.length) {
        clearInterval(interval);
        return;
      }
      addEvent(events[i]);
      i++;
    }, 300);
  }, [addEvent]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const runList = Array.from(runs.values())
    .filter((r) => !agentFilter || r.agent_name === agentFilter)
    .sort((a, b) => b.started_at.localeCompare(a.started_at));

  const selectedRun = selectedRunId ? runs.get(selectedRunId) : null;
  const agents = Array.from(new Set(Array.from(runs.values()).map((r) => r.agent_name)));

  return (
    <div>
      {/* Top bar */}
      <div className="flex items-center gap-3 mb-5 flex-wrap">
        {/* Connection status */}
        <div className="flex items-center gap-1.5">
          <div
            className={`w-2.5 h-2.5 rounded-full ${connected ? 'bg-success' : 'bg-error'}`}
          />
          <span className="text-[13px] text-text-muted">
            {connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>

        <Button
          onClick={connected ? disconnect : connect}
          variant={connected ? 'secondary' : 'primary'}
          className={connected ? 'text-error border-error' : ''}
        >
          {connected ? 'Disconnect' : 'Connect SSE'}
        </Button>

        <Button variant="secondary" onClick={startDemo}>
          Demo Mode
        </Button>

        {agents.length > 0 && (
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="px-3 py-1.5 rounded-md border border-border text-[13px] bg-bg-surface"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        )}

        {runs.size > 0 && (
          <Button
            variant="secondary"
            onClick={() => {
              setRuns(new Map());
              setSelectedRunId(null);
            }}
          >
            Clear
          </Button>
        )}
      </div>

      {/* Main content */}
      <div className="flex gap-5 min-h-[500px]">
        {/* Left panel — run list */}
        <div className="w-[280px] bg-bg-surface rounded-lg border border-border overflow-hidden shrink-0">
          <div className="px-4 py-3 border-b border-border font-semibold text-sm text-text-secondary">
            Runs ({runList.length})
          </div>
          <div className="max-h-[450px] overflow-auto">
            {runList.length === 0 && (
              <div className="p-5 text-text-muted text-[13px] text-center">
                No runs yet
              </div>
            )}
            {runList.map((run) => {
              const isSelected = run.run_id === selectedRunId;
              return (
                <button
                  key={run.run_id}
                  onClick={() => setSelectedRunId(run.run_id)}
                  className={`block w-full px-4 py-2.5 border-none border-b border-border cursor-pointer text-left font-[inherit] ${
                    isSelected ? 'bg-primary-light' : 'bg-bg-surface hover:bg-bg-subtle'
                  }`}
                >
                  <div className="flex items-center gap-1.5 mb-0.5">
                    <div
                      className={`w-[7px] h-[7px] rounded-full ${
                        run.status === 'running'
                          ? 'bg-success'
                          : run.status === 'error'
                            ? 'bg-error'
                            : 'bg-text-muted'
                      }`}
                    />
                    <span className="font-semibold text-[13px]">
                      {run.agent_name}
                    </span>
                  </div>
                  <div className="text-[11px] text-text-muted">
                    {run.run_id?.slice(0, 16) ?? '—'} — {run.events?.length ?? 0} events
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right panel — event timeline */}
        <div className="flex-1 bg-bg-surface rounded-lg border border-border p-5 overflow-auto max-h-[550px]">
          {selectedRun ? (
            <>
              <div className="mb-4">
                <h3 className="m-0 text-base">
                  {selectedRun.agent_name}
                  <span className="font-normal text-text-muted ml-2 text-[13px]">
                    {selectedRun.run_id}
                  </span>
                </h3>
              </div>
              <EventTimeline events={selectedRun.events} />
            </>
          ) : (
            <div className="text-text-muted text-center p-10">
              Select a run from the left panel, or click &quot;Demo Mode&quot; to see a sample execution.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
