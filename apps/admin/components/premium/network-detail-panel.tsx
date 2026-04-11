'use client';

import { Card } from '@/components/ui/legacy';
import { X } from 'lucide-react';

interface NodeDetail {
  id: string;
  tokens: number;
  runs: number;
  error_rate: number;
}

interface EdgeDetail {
  source: string;
  target: string;
  weight: number;
}

interface NetworkDetailPanelProps {
  selectedNode: NodeDetail | null;
  selectedEdge: EdgeDetail | null;
  onClose: () => void;
}

export function NetworkDetailPanel({
  selectedNode,
  selectedEdge,
  onClose,
}: NetworkDetailPanelProps) {
  if (!selectedNode && !selectedEdge) return null;

  return (
    <Card className="!p-4 min-w-[240px]">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold m-0">
          {selectedNode ? selectedNode.id : `${selectedEdge!.source} \u2192 ${selectedEdge!.target}`}
        </h4>
        <button
          type="button"
          onClick={onClose}
          className="text-text-muted hover:text-text-primary bg-transparent border-none cursor-pointer p-0"
        >
          <X size={14} />
        </button>
      </div>

      {selectedNode && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          <span className="text-text-muted">Total Runs</span>
          <span className="font-semibold text-text-primary">{selectedNode.runs}</span>
          <span className="text-text-muted">Total Tokens</span>
          <span className="font-semibold text-text-primary">
            {selectedNode.tokens.toLocaleString()}
          </span>
          <span className="text-text-muted">Error Rate</span>
          <span
            className={`font-semibold ${
              selectedNode.error_rate > 0.05
                ? 'text-error'
                : selectedNode.error_rate > 0.02
                  ? 'text-warning'
                  : 'text-success'
            }`}
          >
            {(selectedNode.error_rate * 100).toFixed(1)}%
          </span>
          <span className="text-text-muted">Avg Tokens/Run</span>
          <span className="font-semibold text-text-primary">
            {selectedNode.runs > 0
              ? Math.round(selectedNode.tokens / selectedNode.runs).toLocaleString()
              : '\u2014'}
          </span>
        </div>
      )}

      {selectedEdge && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
          <span className="text-text-muted">Source</span>
          <span className="font-semibold text-text-primary">{selectedEdge.source}</span>
          <span className="text-text-muted">Target</span>
          <span className="font-semibold text-text-primary">{selectedEdge.target}</span>
          <span className="text-text-muted">Interactions</span>
          <span className="font-semibold text-text-primary">{selectedEdge.weight}</span>
        </div>
      )}
    </Card>
  );
}
