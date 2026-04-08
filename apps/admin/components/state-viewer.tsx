'use client';

import { useState } from 'react';

interface StateViewerProps {
  data: Record<string, unknown>;
  isDelta?: boolean;
}

export function StateViewer({ data, isDelta }: StateViewerProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={`border rounded-md overflow-hidden ${
        isDelta
          ? 'border-warning bg-warning-light'
          : 'border-border bg-bg-subtle'
      }`}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between w-full px-3 py-2 bg-transparent border-none cursor-pointer text-[13px] font-[inherit] text-left"
      >
        <span className="text-warning-dark font-semibold">
          {isDelta ? 'State Delta' : 'State Snapshot'}
        </span>
        <span className="text-text-muted">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <div className="px-3 pb-3">
          <pre className="m-0 p-2 bg-bg-subtle rounded overflow-auto max-h-[300px] text-[11px]">
            {JSON.stringify(data, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
