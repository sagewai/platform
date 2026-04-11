'use client';

import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Check, X, Clock, Loader2 } from 'lucide-react';

export interface CanvasNodeData {
  label: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'waiting';
  model?: string;
  strategy?: string;
  tokens?: number;
  cost?: number;
  duration?: number;
  output?: string;
  error?: string;
  input?: string;
  [key: string]: unknown;
}

const statusStyles: Record<string, string> = {
  pending: 'border-border bg-bg-subtle text-text-muted',
  running: 'border-primary bg-primary/10 text-primary',
  completed: 'border-success bg-success/10 text-success',
  failed: 'border-error bg-error/10 text-error',
  waiting: 'border-warning bg-warning/10 text-warning',
};

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'completed':
      return <Check size={14} className="text-success" />;
    case 'failed':
      return <X size={14} className="text-error" />;
    case 'waiting':
      return <Clock size={14} className="text-warning" />;
    case 'running':
      return <Loader2 size={14} className="text-primary animate-spin" />;
    default:
      return <div className="w-2 h-2 rounded-full bg-text-muted" />;
  }
}

function CanvasNodeComponent({ data }: NodeProps) {
  const nodeData = data as unknown as CanvasNodeData;
  const status = nodeData.status || 'pending';
  const style = statusStyles[status] || statusStyles.pending;

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-border !w-2 !h-2" />
      <div
        className={`rounded-lg border-2 px-4 py-3 min-w-[160px] shadow-sm transition-all ${style} ${
          status === 'running' ? 'animate-pulse' : ''
        }`}
      >
        <div className="flex items-center gap-2">
          <StatusIcon status={status} />
          <span className="font-semibold text-sm truncate max-w-[120px]">{nodeData.label}</span>
        </div>
        {nodeData.model && (
          <div className="text-[10px] text-text-muted font-[family-name:var(--font-mono)] mt-1 truncate">
            {nodeData.model}
          </div>
        )}
        {nodeData.tokens != null && nodeData.tokens > 0 && (
          <div className="text-[10px] text-text-muted mt-0.5">
            {nodeData.tokens.toLocaleString()} tok
            {nodeData.duration != null && ` \u00B7 ${nodeData.duration}s`}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-border !w-2 !h-2" />
    </>
  );
}

export const CanvasNode = memo(CanvasNodeComponent);
