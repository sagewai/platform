'use client';

import type { ValidationResult } from '@/utils/playground-api';
import type { WorkflowDefinition, WorkflowNode } from '@/utils/workflow-types';
import {
  isAgentStep,
  isSequentialNode,
  isParallelNode,
  isLoopNode,
} from '@/utils/workflow-types';
import type { LoopNode } from '@/utils/workflow-types';
import { Brain, Sparkles } from 'lucide-react';

interface Props {
  validation: ValidationResult | null;
  definition?: WorkflowDefinition | null;
}

function AgentNode({
  name,
  variant = 'default',
  hasContext = false,
  hasDirectives = false,
}: {
  name: string;
  variant?: 'default' | 'ref' | 'inline';
  hasContext?: boolean;
  hasDirectives?: boolean;
}) {
  const colors =
    variant === 'ref'
      ? 'border-info bg-info/10 text-info'
      : variant === 'inline'
        ? 'border-warning bg-warning/10 text-warning'
        : 'border-border bg-bg-surface text-text-primary';

  return (
    <div
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium whitespace-nowrap border ${colors}`}
    >
      <div
        className={`w-2 h-2 rounded-full shrink-0 ${
          variant === 'ref' ? 'bg-info' : variant === 'inline' ? 'bg-warning' : 'bg-primary'
        }`}
      />
      {name}
      {hasContext && (
        <Brain className="w-3 h-3 text-primary/70 shrink-0" aria-label="Has context" />
      )}
      {hasDirectives && (
        <Sparkles className="w-3 h-3 text-warning/70 shrink-0" aria-label="Has directives" />
      )}
    </div>
  );
}

function Arrow({ label }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center px-1">
      <div className="text-text-muted text-lg leading-none">&rarr;</div>
      {label && <div className="text-[9px] text-text-muted">{label}</div>}
    </div>
  );
}

function getVariant(
  agentName: string,
  def?: WorkflowDefinition | null,
): 'ref' | 'inline' | 'default' {
  if (!def) return 'default';
  const agentDef = def.agents[agentName];
  if (!agentDef) return 'default';
  return agentDef.ref ? 'ref' : 'inline';
}

/* ─── Structured node renderer ─── */

function NodeView({
  node,
  def,
}: {
  node: WorkflowNode;
  def?: WorkflowDefinition | null;
}) {
  if (isAgentStep(node)) {
    return <AgentNode name={node.agent} variant={getVariant(node.agent, def)} />;
  }

  if (isSequentialNode(node)) {
    return (
      <div className="flex items-center flex-wrap gap-1">
        {node.steps.map((step, i) => (
          <div key={i} className="flex items-center">
            <NodeView node={step} def={def} />
            {i < node.steps.length - 1 && <Arrow />}
          </div>
        ))}
      </div>
    );
  }

  if (isParallelNode(node)) {
    return (
      <div className="flex items-center gap-1">
        {/* Fan-out marker */}
        <div className="flex flex-col items-center gap-0.5 text-info text-[10px] font-semibold">
          <span>⟨</span>
          <span className="text-[8px]">PAR</span>
        </div>
        <div className="flex flex-col gap-1 border-l-2 border-r-2 border-info/30 px-2 py-1 rounded">
          {node.agents.map((a) => (
            <AgentNode key={a} name={a} variant={getVariant(a, def)} />
          ))}
        </div>
        {/* Fan-in marker */}
        <div className="flex flex-col items-center gap-0.5 text-info text-[10px] font-semibold">
          <span>⟩</span>
          <span className="text-[8px]">merge</span>
        </div>
      </div>
    );
  }

  // LoopNode
  const loop = node as LoopNode;
  return (
    <div className="flex items-center gap-1">
      <div className="flex flex-col items-center border border-warning/30 rounded-lg px-2 py-1.5 bg-warning/5">
        <AgentNode name={loop.agent} variant={getVariant(loop.agent, def)} />
        <div className="flex items-center gap-1 mt-1 text-[9px] text-warning">
          <span>↻</span>
          <span>×{loop.max_iterations}</span>
          {loop.stop_condition && (
            <span className="text-text-muted ml-1">until &quot;{loop.stop_condition}&quot;</span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Main component ─── */

export function PipelineGraph({ validation, definition }: Props) {
  if (!validation || !validation.valid) {
    return (
      <div className="p-6 text-center text-[13px] bg-bg-subtle rounded-lg border border-border">
        {validation && !validation.valid ? (
          <span className="text-error">Invalid: {validation.error}</span>
        ) : (
          <span className="text-text-muted">
            Define a workflow to see the pipeline graph.
          </span>
        )}
      </div>
    );
  }

  /* ─── Structured rendering (from WorkflowDefinition) ─── */
  if (definition && definition.name) {
    return (
      <div className="bg-bg-subtle rounded-lg border border-border p-5">
        <div className="text-xs text-text-muted mb-3 flex items-center gap-2 flex-wrap">
          <strong>{definition.name}</strong>
          {definition.description && (
            <span className="text-text-muted"> — {definition.description}</span>
          )}
          {definition.default_model && (
            <span className="text-[10px] font-[family-name:var(--font-mono)] bg-bg-surface px-1.5 py-0.5 rounded border border-border">
              default: {definition.default_model}
            </span>
          )}
        </div>
        <NodeView node={definition.workflow} def={definition} />
        {/* Legend */}
        <div className="flex items-center gap-3 mt-3 pt-2 border-t border-border/50">
          <div className="flex items-center gap-1 text-[10px] text-text-muted">
            <div className="w-2 h-2 rounded-full bg-info" /> ref
          </div>
          <div className="flex items-center gap-1 text-[10px] text-text-muted">
            <div className="w-2 h-2 rounded-full bg-warning" /> inline
          </div>
          <div className="flex items-center gap-1 text-[10px] text-text-muted">
            <Brain className="w-3 h-3 text-primary/70" /> context
          </div>
          <div className="flex items-center gap-1 text-[10px] text-text-muted">
            <Sparkles className="w-3 h-3 text-warning/70" /> directives
          </div>
        </div>
      </div>
    );
  }

  /* ─── Flat rendering fallback (from ValidationResult only) ─── */
  const agents = validation.agents ?? [];

  if (agents.length === 0) {
    return (
      <div className="p-6 text-center text-[13px] text-text-muted bg-bg-subtle rounded-lg border border-border">
        No agents defined.
      </div>
    );
  }

  return (
    <div className="bg-bg-subtle rounded-lg border border-border p-5">
      <div className="text-xs text-text-muted mb-3">
        <strong>{validation.name}</strong>
        {validation.description ? ` — ${validation.description}` : ''}
      </div>
      <div className="flex items-center flex-wrap gap-1">
        {agents.map((agent, i) => (
          <div key={agent.name} className="flex items-center">
            <AgentNode
              name={agent.name}
              hasContext={agent.has_context}
              hasDirectives={agent.has_directives}
            />
            {i < agents.length - 1 && <Arrow />}
          </div>
        ))}
      </div>
    </div>
  );
}
