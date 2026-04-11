'use client';

import { Button, Badge, Select } from '@/components/ui/legacy';
import type { WorkflowNode, AgentNodeDef } from '@/utils/workflow-types';
import { isAgentStep, isSequentialNode, isParallelNode, isLoopNode } from '@/utils/workflow-types';

interface Props {
  index: number;
  node: WorkflowNode;
  agentNames: string[];
  onChange: (node: WorkflowNode) => void;
  onRemove: () => void;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
}

export function WorkflowStepCard({
  index,
  node,
  agentNames,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
}: Props) {
  const typeLabel = isAgentStep(node)
    ? 'Agent'
    : isParallelNode(node)
      ? 'Parallel'
      : isLoopNode(node)
        ? 'Loop'
        : 'Sequential';

  const typeBadge =
    typeLabel === 'Agent'
      ? 'default'
      : typeLabel === 'Parallel'
        ? 'info'
        : typeLabel === 'Loop'
          ? 'warning'
          : 'success';

  return (
    <div className="border border-border rounded-lg p-3 bg-bg-surface">
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] text-text-muted font-semibold w-5 shrink-0">#{index + 1}</span>
        <Badge variant={typeBadge as 'default'}>{typeLabel}</Badge>
        <div className="flex-1" />
        <button
          type="button"
          disabled={!onMoveUp}
          onClick={onMoveUp}
          className="text-[10px] text-text-muted hover:text-primary disabled:opacity-30 border-none bg-transparent cursor-pointer px-1"
          title="Move up"
        >
          ▲
        </button>
        <button
          type="button"
          disabled={!onMoveDown}
          onClick={onMoveDown}
          className="text-[10px] text-text-muted hover:text-primary disabled:opacity-30 border-none bg-transparent cursor-pointer px-1"
          title="Move down"
        >
          ▼
        </button>
        <button
          type="button"
          onClick={onRemove}
          className="text-[10px] text-error/70 hover:text-error border-none bg-transparent cursor-pointer px-1"
          title="Remove step"
        >
          ✕
        </button>
      </div>

      {/* Agent step */}
      {isAgentStep(node) && (
        <Select
          value={node.agent}
          onChange={(e) => onChange({ agent: e.target.value })}
        >
          <option value="">Select agent...</option>
          {agentNames.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </Select>
      )}

      {/* Parallel block */}
      {isParallelNode(node) && (
        <div className="flex flex-col gap-1.5">
          <p className="text-[11px] text-text-muted m-0">Agents run in parallel:</p>
          {node.agents.map((a, i) => (
            <div key={`${a}-${i}`} className="flex items-center gap-1.5">
              <Select
                value={a}
                onChange={(e) => {
                  const agents = [...node.agents];
                  agents[i] = e.target.value;
                  onChange({ ...node, agents });
                }}
              >
                <option value="">Select agent...</option>
                {agentNames.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </Select>
              <button
                type="button"
                onClick={() => {
                  const agents = node.agents.filter((_, j) => j !== i);
                  onChange({ ...node, agents });
                }}
                className="text-[10px] text-error/70 hover:text-error border-none bg-transparent cursor-pointer shrink-0"
              >
                ✕
              </button>
            </div>
          ))}
          <Button
            variant="ghost"
            className="text-xs self-start"
            onClick={() => onChange({ ...node, agents: [...node.agents, ''] })}
          >
            + Add parallel agent
          </Button>
        </div>
      )}

      {/* Loop block */}
      {isLoopNode(node) && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text-muted shrink-0">Agent:</span>
            <Select
              value={node.agent}
              onChange={(e) => onChange({ ...node, agent: e.target.value })}
            >
              <option value="">Select agent...</option>
              {agentNames.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text-muted shrink-0">Max iterations:</span>
            <input
              type="number"
              min={1}
              max={20}
              value={node.max_iterations}
              onChange={(e) =>
                onChange({ ...node, max_iterations: parseInt(e.target.value, 10) || 3 })
              }
              className="w-16 px-2 py-1 rounded border border-border text-xs bg-bg-surface"
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text-muted shrink-0">Stop when output contains:</span>
            <input
              type="text"
              value={node.stop_condition ?? ''}
              onChange={(e) =>
                onChange({ ...node, stop_condition: e.target.value || undefined })
              }
              placeholder="(optional)"
              className="flex-1 px-2 py-1 rounded border border-border text-xs bg-bg-surface"
            />
          </div>
        </div>
      )}
    </div>
  );
}
