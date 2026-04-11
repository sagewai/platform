'use client';

import { useState } from 'react';

interface ToolCallDetailProps {
  toolName: string;
  arguments_: Record<string, unknown>;
  result?: unknown;
  durationMs?: number;
}

export function ToolCallDetail({ toolName, arguments_, result, durationMs }: ToolCallDetailProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-border rounded-md bg-primary-light overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between w-full px-3 py-2 bg-transparent border-none cursor-pointer text-[13px] font-[inherit] text-left"
      >
        <span>
          <strong className="text-primary">{toolName}</strong>
          {durationMs != null && (
            <span className="text-text-muted ml-2 text-[11px]">
              {durationMs}ms
            </span>
          )}
        </span>
        <span className="text-text-muted">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="px-3 pb-3 text-xs">
          <div className="mb-2">
            <div className="font-semibold text-text-muted mb-1">Arguments</div>
            <pre className="m-0 p-2 bg-bg-subtle rounded overflow-auto max-h-[200px] text-[11px]">
              {JSON.stringify(arguments_, null, 2)}
            </pre>
          </div>
          {result !== undefined && (
            <div>
              <div className="font-semibold text-text-muted mb-1">Result</div>
              <pre className="m-0 p-2 bg-bg-subtle rounded overflow-auto max-h-[200px] text-[11px]">
                {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
