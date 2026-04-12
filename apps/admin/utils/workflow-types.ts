/**
 * Workflow type definitions + YAML serialization helpers.
 *
 * Mirrors the Python YAML workflow spec in
 * packages/sdk/sagewai/core/yaml_workflow.py
 */

import yaml from 'js-yaml';

/* ─── Agent node definition ─── */

export interface AgentNodeDef {
  /** Reference an existing playground agent by name */
  ref?: string;
  /** LLM model id (inline agent) */
  model?: string;
  /** System prompt (inline agent) */
  system_prompt?: string;
  /** Sampling temperature */
  temperature?: number;
  /** Max tool-calling iterations */
  max_iterations?: number;
  /** Max output tokens */
  max_tokens?: number;
  /** Ordered fallback models */
  fallback_models?: string[];
  /** Custom API base URL (e.g. for LM Studio models) */
  api_base?: string;
}

/* ─── Workflow node types ─── */

export interface AgentStep {
  agent: string;
}

export interface SequentialNode {
  type: 'sequential';
  steps: WorkflowNode[];
}

export interface ParallelNode {
  type: 'parallel';
  agents: string[];
}

export interface LoopNode {
  type: 'loop';
  agent: string;
  max_iterations: number;
  stop_condition?: string;
}

export type WorkflowNode = AgentStep | SequentialNode | ParallelNode | LoopNode;

/* ─── Top-level workflow definition ─── */

export interface WorkflowDefinition {
  name: string;
  description: string;
  agents: Record<string, AgentNodeDef>;
  workflow: WorkflowNode;
  /** Default model applied to inline agents without an explicit model */
  default_model?: string;
  /** Workflow-level fallback models */
  fallback_models?: string[];
}

/* ─── Type guards ─── */

export function isAgentStep(node: WorkflowNode): node is AgentStep {
  return 'agent' in node && !('type' in node);
}

export function isSequentialNode(node: WorkflowNode): node is SequentialNode {
  return 'type' in node && node.type === 'sequential';
}

export function isParallelNode(node: WorkflowNode): node is ParallelNode {
  return 'type' in node && node.type === 'parallel';
}

export function isLoopNode(node: WorkflowNode): node is LoopNode {
  return 'type' in node && node.type === 'loop';
}

/* ─── Serialization: definition → YAML string ─── */

function nodeToPlain(node: WorkflowNode): Record<string, unknown> {
  if (isAgentStep(node)) {
    return { agent: node.agent };
  }
  if (isSequentialNode(node)) {
    return {
      type: 'sequential',
      steps: node.steps.map(nodeToPlain),
    };
  }
  if (isParallelNode(node)) {
    return { type: 'parallel', agents: node.agents };
  }
  // After exhaustive checks above, remaining case is LoopNode
  const loop = node as LoopNode;
  const obj: Record<string, unknown> = {
    type: 'loop',
    agent: loop.agent,
    max_iterations: loop.max_iterations,
  };
  if (loop.stop_condition) obj.stop_condition = loop.stop_condition;
  return obj;
}

function agentDefToPlain(def: AgentNodeDef): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (def.ref) {
    out.ref = def.ref;
    return out; // ref agents don't need other fields
  }
  if (def.model) out.model = def.model;
  if (def.system_prompt) out.system_prompt = def.system_prompt;
  if (def.temperature !== undefined) out.temperature = def.temperature;
  if (def.max_iterations !== undefined) out.max_iterations = def.max_iterations;
  if (def.max_tokens !== undefined) out.max_tokens = def.max_tokens;
  if (def.fallback_models?.length) out.fallback_models = def.fallback_models;
  if (def.api_base) out.api_base = def.api_base;
  return out;
}

export function workflowToYaml(def: WorkflowDefinition): string {
  const plain: Record<string, unknown> = {
    name: def.name,
  };
  if (def.description) plain.description = def.description;
  if (def.default_model) plain.default_model = def.default_model;
  if (def.fallback_models?.length) plain.fallback_models = def.fallback_models;

  // Only include agents that are actually referenced in steps
  const used = new Set(referencedAgents(def.workflow));
  const agents: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(def.agents)) {
    if (used.has(k)) {
      agents[k] = agentDefToPlain(v);
    }
  }
  plain.agents = agents;
  plain.workflow = nodeToPlain(def.workflow);

  return yaml.dump(plain, { lineWidth: 100, noRefs: true, quotingType: '"' });
}

/* ─── Deserialization: YAML string → definition ─── */

function parseNode(raw: unknown): WorkflowNode | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, unknown>;

  if ('agent' in obj && !('type' in obj)) {
    return { agent: String(obj.agent) } as AgentStep;
  }

  const type = obj.type as string | undefined;
  if (!type) return null;

  if (type === 'sequential') {
    const steps = Array.isArray(obj.steps)
      ? (obj.steps.map(parseNode).filter(Boolean) as WorkflowNode[])
      : [];
    return { type: 'sequential', steps };
  }

  if (type === 'parallel') {
    const agents = Array.isArray(obj.agents)
      ? obj.agents.map(String)
      : [];
    return { type: 'parallel', agents };
  }

  if (type === 'loop') {
    return {
      type: 'loop',
      agent: String(obj.agent ?? ''),
      max_iterations: Number(obj.max_iterations ?? 3),
      stop_condition: obj.stop_condition ? String(obj.stop_condition) : undefined,
    };
  }

  return null;
}

function parseAgentDef(raw: unknown): AgentNodeDef {
  if (!raw || typeof raw !== 'object') return {};
  const obj = raw as Record<string, unknown>;
  const def: AgentNodeDef = {};
  if (obj.ref) { def.ref = String(obj.ref); return def; }
  if (obj.model) def.model = String(obj.model);
  if (obj.system_prompt) def.system_prompt = String(obj.system_prompt);
  if (obj.temperature !== undefined) def.temperature = Number(obj.temperature);
  if (obj.max_iterations !== undefined) def.max_iterations = Number(obj.max_iterations);
  if (obj.max_tokens !== undefined) def.max_tokens = Number(obj.max_tokens);
  if (Array.isArray(obj.fallback_models)) def.fallback_models = obj.fallback_models.map(String);
  if (obj.api_base) def.api_base = String(obj.api_base);
  return def;
}

export function yamlToWorkflow(yamlStr: string): WorkflowDefinition | null {
  try {
    const data = yaml.load(yamlStr) as Record<string, unknown> | null;
    if (!data || typeof data !== 'object') return null;
    if (!data.name) return null;

    const agentDefs = (data.agents ?? {}) as Record<string, unknown>;
    const agents: Record<string, AgentNodeDef> = {};
    for (const [k, v] of Object.entries(agentDefs)) {
      agents[k] = parseAgentDef(v);
    }

    const workflowNode = parseNode(data.workflow);
    if (!workflowNode) return null;

    return {
      name: String(data.name),
      description: data.description ? String(data.description) : '',
      agents,
      workflow: workflowNode,
      default_model: data.default_model ? String(data.default_model) : undefined,
      fallback_models: Array.isArray(data.fallback_models)
        ? data.fallback_models.map(String)
        : undefined,
    };
  } catch {
    return null;
  }
}

/* ─── Helpers for the visual builder ─── */

/** Create a blank workflow definition */
export function emptyWorkflow(): WorkflowDefinition {
  return {
    name: '',
    description: '',
    agents: {},
    workflow: { type: 'sequential', steps: [] },
  };
}

/** List all agent names referenced in a workflow node tree */
export function referencedAgents(node: WorkflowNode): string[] {
  if (isAgentStep(node)) return [node.agent];
  if (isSequentialNode(node)) return node.steps.flatMap(referencedAgents);
  if (isParallelNode(node)) return [...node.agents];
  return [(node as LoopNode).agent];
}
