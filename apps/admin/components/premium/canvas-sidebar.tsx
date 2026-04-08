'use client';

import { Badge } from '@sagecurator/ui';
import { X } from 'lucide-react';
import type { CanvasNodeData } from './canvas-node';

interface Props {
  data: CanvasNodeData | null;
  onClose: () => void;
}

function statusVariant(status: string): 'success' | 'error' | 'info' | 'warning' | 'default' {
  switch (status) {
    case 'completed':
      return 'success';
    case 'failed':
      return 'error';
    case 'running':
      return 'info';
    case 'waiting':
      return 'warning';
    default:
      return 'default';
  }
}

export function CanvasSidebar({ data, onClose }: Props) {
  if (!data) return null;

  return (
    <div className="absolute right-0 top-0 h-full w-80 bg-bg-surface border-l border-border shadow-lg z-50 overflow-y-auto animate-in slide-in-from-right duration-200">
      <div className="flex items-center justify-between p-4 border-b border-border">
        <h3 className="text-sm font-semibold truncate">{data.label}</h3>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded hover:bg-bg-subtle border-none bg-transparent cursor-pointer text-text-muted"
        >
          <X size={16} />
        </button>
      </div>

      <div className="p-4 space-y-4">
        {/* Status */}
        <div>
          <div className="text-[10px] text-text-muted uppercase mb-1">Status</div>
          <Badge variant={statusVariant(data.status)}>{data.status}</Badge>
        </div>

        {/* Model & Strategy */}
        {data.model && (
          <div>
            <div className="text-[10px] text-text-muted uppercase mb-1">Model</div>
            <div className="text-sm font-[family-name:var(--font-mono)]">{data.model}</div>
          </div>
        )}
        {data.strategy && (
          <div>
            <div className="text-[10px] text-text-muted uppercase mb-1">Strategy</div>
            <div className="text-sm">{data.strategy}</div>
          </div>
        )}

        {/* Stats */}
        {(data.tokens != null || data.cost != null || data.duration != null) && (
          <div className="grid grid-cols-3 gap-2">
            {data.tokens != null && (
              <div className="bg-bg-subtle rounded p-2 text-center">
                <div className="text-sm font-semibold">{data.tokens.toLocaleString()}</div>
                <div className="text-[10px] text-text-muted">tokens</div>
              </div>
            )}
            {data.cost != null && (
              <div className="bg-bg-subtle rounded p-2 text-center">
                <div className="text-sm font-semibold">${data.cost.toFixed(4)}</div>
                <div className="text-[10px] text-text-muted">cost</div>
              </div>
            )}
            {data.duration != null && (
              <div className="bg-bg-subtle rounded p-2 text-center">
                <div className="text-sm font-semibold">{data.duration}s</div>
                <div className="text-[10px] text-text-muted">duration</div>
              </div>
            )}
          </div>
        )}

        {/* Running state */}
        {data.status === 'running' && (
          <div className="text-sm text-primary">
            Processing
            <span className="inline-flex ml-1">
              <span className="animate-bounce [animation-delay:0ms]">.</span>
              <span className="animate-bounce [animation-delay:150ms]">.</span>
              <span className="animate-bounce [animation-delay:300ms]">.</span>
            </span>
          </div>
        )}

        {/* Output */}
        {data.status === 'completed' && data.output && (
          <div>
            <div className="text-[10px] text-text-muted uppercase mb-1">Output</div>
            <pre className="text-xs bg-bg-subtle p-2 rounded max-h-48 overflow-auto whitespace-pre-wrap font-[family-name:var(--font-mono)]">
              {data.output.length > 500 ? `${data.output.slice(0, 500)}...` : data.output}
            </pre>
          </div>
        )}

        {/* Error */}
        {data.status === 'failed' && data.error && (
          <div>
            <div className="text-[10px] text-text-muted uppercase mb-1">Error</div>
            <pre className="text-xs bg-error/10 text-error p-2 rounded max-h-48 overflow-auto whitespace-pre-wrap font-[family-name:var(--font-mono)]">
              {data.error}
            </pre>
          </div>
        )}

        {/* Input */}
        {data.input && (
          <div>
            <div className="text-[10px] text-text-muted uppercase mb-1">Input</div>
            <pre className="text-xs bg-bg-subtle p-2 rounded max-h-32 overflow-auto whitespace-pre-wrap font-[family-name:var(--font-mono)]">
              {data.input.length > 300 ? `${data.input.slice(0, 300)}...` : data.input}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
