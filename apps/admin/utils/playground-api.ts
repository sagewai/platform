/** API client for playground, strategies, and workflow endpoints. */

import { authFetch } from './auth';

const BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace('/admin', '') ??
  'http://localhost:8000';

export interface AgentSpec {
  name: string;
  model: string;
  system_prompt: string;
  strategy: string;
  temperature: number;
  preset?: string | null;
  top_p?: number | null;
  max_tokens?: number | null;
  frequency_penalty?: number | null;
  presence_penalty?: number | null;
  max_iterations?: number;
  tools?: string[];
  mcp_servers?: string[];
  memory_backends?: string[];
  guardrails?: string[];
  tags?: string[];
  fallback_models?: string[];
  api_base?: string | null;
  auto_learn?: boolean;
  directive_template?: string;
}

export interface InferencePreset {
  name: string;
  temperature: number;
  top_p: number;
}

export interface AdhocAgent {
  name: string;
  model: string;
  strategy: string;
  system_prompt_preview: string;
}

export interface StrategyResult {
  strategy: string;
  output: string;
  total_tokens: number;
  cost_usd: number;
  duration_ms: number;
  steps: number;
  tool_calls: number;
  status: string;
  error: string;
}

export interface StrategyDetail {
  id: string;
  name: string;
  category: string;
  description: string;
  when_to_use: string;
  when_not_to_use: string;
  llm_calls: string;
  cost_level: string;
  prompt_tips: string[];
  example_prompt: string;
}

export interface WorkflowTemplate {
  name: string;
  description: string;
  yaml: string;
}

export interface ValidationResult {
  valid: boolean;
  name?: string;
  description?: string;
  agents?: { name: string; has_context?: boolean; has_directives?: boolean }[];
  error?: string;
}

async function jsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export const playgroundApi = {
  createAgent: (spec: AgentSpec) =>
    authFetch(`${BASE}/playground/agent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(spec),
    }).then((r) => jsonOrThrow(r)),

  listAgents: (): Promise<AdhocAgent[]> =>
    authFetch(`${BASE}/playground/agents`).then((r) => jsonOrThrow(r)),

  getAgentSpec: (name: string): Promise<AgentSpec> =>
    authFetch(`${BASE}/playground/agents/${encodeURIComponent(name)}`).then((r) => jsonOrThrow(r)),

  deleteAgent: (name: string) =>
    authFetch(`${BASE}/playground/agents/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }).then((r) => jsonOrThrow(r)),

  listStrategies: (): Promise<string[]> =>
    authFetch(`${BASE}/playground/strategies`).then((r) => jsonOrThrow(r)),

  listModels: (): Promise<string[]> =>
    authFetch(`${BASE}/playground/models`).then((r) => jsonOrThrow(r)),

  listPresets: (): Promise<InferencePreset[]> =>
    authFetch(`${BASE}/playground/presets`).then((r) => jsonOrThrow(r)),

  listCapabilities: (): Promise<Record<string, Array<{ id: string; name: string; description: string }>>> =>
    authFetch(`${BASE}/playground/capabilities`).then((r) => jsonOrThrow(r)),

  listStrategyOptions: (): Promise<string[]> =>
    authFetch(`${BASE}/strategies/list`).then((r) => jsonOrThrow(r)),

  listStrategyDetails: (): Promise<StrategyDetail[]> =>
    authFetch(`${BASE}/strategies/detail`).then((r) => jsonOrThrow(r)),

  validateWorkflow: (yaml: string): Promise<ValidationResult> =>
    authFetch(`${BASE}/workflows/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml }),
    }).then((r) => jsonOrThrow(r)),

  listTemplates: (): Promise<WorkflowTemplate[]> =>
    authFetch(`${BASE}/workflows/templates`).then((r) => jsonOrThrow(r)),
};

/** Parse SSE lines from a ReadableStream (for POST-based SSE). */
export async function* readSSE(
  response: Response
): AsyncGenerator<{ event: string; data: string }> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = 'message';
  let dataLines: string[] = [];

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const rawLine of lines) {
      // Strip trailing \r from \r\n line endings (SSE wire format uses CRLF)
      const line = rawLine.replace(/\r$/, '');

      if (line === '') {
        // Empty line = end of SSE event — flush accumulated data
        if (dataLines.length > 0) {
          yield { event: currentEvent, data: dataLines.join('\n') };
          dataLines = [];
          currentEvent = 'message';
        }
      } else if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        dataLines.push(line.slice(6));
      } else if (line.startsWith('data:')) {
        // "data:" with no space (empty data line)
        dataLines.push(line.slice(5));
      }
    }
  }

  // Flush any remaining data at stream end
  if (dataLines.length > 0) {
    yield { event: currentEvent, data: dataLines.join('\n') };
  }
}
