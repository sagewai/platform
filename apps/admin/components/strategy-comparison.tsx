'use client';

import { useCallback, useEffect, useState } from 'react';
import { playgroundApi, readSSE } from '@/utils/playground-api';
import { adminApi } from '@/utils/api';
import { authFetch } from '@/utils/auth';
import type { StrategyResult, StrategyDetail } from '@/utils/playground-api';
import type { AvailableModel } from '@/utils/types';
import { Card, Badge, Button } from '@sagecurator/ui';
import {
  ChevronDown,
  ChevronRight,
  Lightbulb,
  Zap,
  DollarSign,
  Brain,
  Target,
  XCircle,
  Copy,
  Check,
} from 'lucide-react';

const BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace('/admin', '') ??
  'http://localhost:8000';

interface ComparisonSummary {
  total_strategies: number;
  fastest: string;
  cheapest: string;
}

const CATEGORY_COLORS: Record<string, string> = {
  reasoning: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  search: 'bg-purple-500/10 text-purple-600 border-purple-500/20',
  reflection: 'bg-amber-500/10 text-amber-600 border-amber-500/20',
  consensus: 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20',
  planning: 'bg-rose-500/10 text-rose-600 border-rose-500/20',
};

const COST_INDICATOR: Record<string, { label: string; dots: number }> = {
  low: { label: 'Low', dots: 1 },
  'low-medium': { label: 'Low-Med', dots: 2 },
  medium: { label: 'Medium', dots: 2 },
  'medium-high': { label: 'Med-High', dots: 3 },
  high: { label: 'High', dots: 3 },
  'very high': { label: 'Very High', dots: 4 },
};

function CostDots({ level }: { level: string }) {
  const info = COST_INDICATOR[level] || { label: level, dots: 2 };
  return (
    <span className="inline-flex items-center gap-1">
      {Array.from({ length: 4 }, (_, i) => (
        <span
          key={i}
          className={`w-1.5 h-1.5 rounded-full ${
            i < info.dots ? 'bg-current' : 'bg-current/20'
          }`}
        />
      ))}
      <span className="ml-0.5 text-[11px]">{info.label}</span>
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [text]);
  return (
    <button
      onClick={copy}
      className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-subtle transition-colors"
      title="Copy to prompt"
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Strategy Guide Card                                                 */
/* ------------------------------------------------------------------ */

function StrategyGuideCard({
  detail,
  isExpanded,
  onToggle,
  onUseExample,
}: {
  detail: StrategyDetail;
  isExpanded: boolean;
  onToggle: () => void;
  onUseExample: (prompt: string) => void;
}) {
  const catColor = CATEGORY_COLORS[detail.category] || 'bg-bg-subtle text-text-muted border-border';

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-bg-surface">
      {/* Collapsed header */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-bg-subtle/50 transition-colors"
      >
        <span className="text-text-muted flex-shrink-0">
          {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm">{detail.name}</span>
            <span className={`text-[11px] px-1.5 py-0.5 rounded border ${catColor}`}>
              {detail.category}
            </span>
          </div>
          <p className="text-[13px] text-text-secondary mt-0.5 truncate">
            {detail.description}
          </p>
        </div>
        <div className="flex-shrink-0 text-text-muted text-[12px] flex items-center gap-3">
          <span className="flex items-center gap-1" title="LLM calls">
            <Zap size={12} /> {detail.llm_calls}
          </span>
          <span className="flex items-center gap-1" title="Cost level">
            <DollarSign size={12} /> <CostDots level={detail.cost_level} />
          </span>
        </div>
      </button>

      {/* Expanded detail */}
      {isExpanded && (
        <div className="px-4 pb-4 border-t border-border/50">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3">
            {/* When to use */}
            <div className="flex gap-2">
              <Target size={14} className="text-emerald-500 flex-shrink-0 mt-0.5" />
              <div>
                <div className="text-[12px] font-medium text-emerald-600 mb-0.5">When to use</div>
                <p className="text-[13px] text-text-secondary leading-relaxed">{detail.when_to_use}</p>
              </div>
            </div>
            {/* When NOT to use */}
            <div className="flex gap-2">
              <XCircle size={14} className="text-red-400 flex-shrink-0 mt-0.5" />
              <div>
                <div className="text-[12px] font-medium text-red-500 mb-0.5">When NOT to use</div>
                <p className="text-[13px] text-text-secondary leading-relaxed">{detail.when_not_to_use}</p>
              </div>
            </div>
          </div>

          {/* Prompt tips */}
          {detail.prompt_tips.length > 0 && (
            <div className="mt-4">
              <div className="flex items-center gap-1.5 mb-2">
                <Lightbulb size={14} className="text-amber-500" />
                <span className="text-[12px] font-medium text-amber-600">Prompting Tips</span>
              </div>
              <ul className="space-y-1.5">
                {detail.prompt_tips.map((tip, i) => (
                  <li key={i} className="flex gap-2 text-[13px] text-text-secondary leading-relaxed">
                    <span className="text-text-muted flex-shrink-0 mt-px">&bull;</span>
                    <span>{tip}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Example prompt */}
          {detail.example_prompt && (
            <div className="mt-3 bg-bg-subtle rounded-md p-3 flex items-start gap-2">
              <Brain size={14} className="text-primary flex-shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <div className="text-[11px] font-medium text-primary mb-1">Example Prompt</div>
                <p className="text-[13px] text-text-secondary leading-relaxed italic">
                  &ldquo;{detail.example_prompt}&rdquo;
                </p>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <CopyButton text={detail.example_prompt} />
                <button
                  onClick={() => onUseExample(detail.example_prompt)}
                  className="text-[11px] text-primary hover:text-primary-hover font-medium px-1.5 py-0.5 rounded hover:bg-primary-light transition-colors"
                >
                  Use
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Decision Helper                                                     */
/* ------------------------------------------------------------------ */

const DECISION_ROWS = [
  {
    need: 'I need the agent to use tools (search, code, APIs)',
    strategies: ['react', 'planning', 'planning_simple'],
  },
  {
    need: 'I want the fastest, cheapest answer',
    strategies: ['chain_of_thought'],
  },
  {
    need: 'I need a polished, iteratively refined output',
    strategies: ['evaluator_optimizer', 'reflexion'],
  },
  {
    need: 'I want to reduce errors on factual / math questions',
    strategies: ['majority_vote', 'chain_of_thought'],
  },
  {
    need: 'I need multiple perspectives on a complex topic',
    strategies: ['debate'],
  },
  {
    need: 'The output must match a strict schema or format',
    strategies: ['self_correction'],
  },
  {
    need: 'I need to explore many solution paths',
    strategies: ['tree_of_thoughts', 'lats'],
  },
  {
    need: 'I have a multi-step task with changing requirements',
    strategies: ['planning'],
  },
];

function DecisionHelper({
  details,
  onSelect,
}: {
  details: StrategyDetail[];
  onSelect: (ids: string[]) => void;
}) {
  const nameMap = Object.fromEntries(details.map((d) => [d.id, d.name]));

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="bg-bg-subtle border-b border-border">
            <th className="text-left px-4 py-2 font-medium text-text-secondary">I need to...</th>
            <th className="text-left px-4 py-2 font-medium text-text-secondary">Best strategies</th>
            <th className="px-4 py-2 w-16"></th>
          </tr>
        </thead>
        <tbody>
          {DECISION_ROWS.map((row, i) => (
            <tr key={i} className="border-b border-border/50 last:border-0 hover:bg-bg-subtle/30 transition-colors">
              <td className="px-4 py-2.5 text-text-primary">{row.need}</td>
              <td className="px-4 py-2.5">
                <div className="flex gap-1.5 flex-wrap">
                  {row.strategies.map((s) => (
                    <span
                      key={s}
                      className={`text-[11px] px-1.5 py-0.5 rounded border ${
                        CATEGORY_COLORS[details.find((d) => d.id === s)?.category || ''] ||
                        'bg-bg-subtle text-text-muted border-border'
                      }`}
                    >
                      {nameMap[s] || s}
                    </span>
                  ))}
                </div>
              </td>
              <td className="px-4 py-2.5 text-center">
                <button
                  onClick={() => onSelect(row.strategies)}
                  className="text-[11px] text-primary hover:text-primary-hover font-medium hover:underline"
                >
                  Select
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main Component                                                      */
/* ------------------------------------------------------------------ */

export function StrategyComparison() {
  const [strategies, setStrategies] = useState<string[]>([]);
  const [details, setDetails] = useState<StrategyDetail[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set(['react']));
  const [model, setModel] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [prompt, setPrompt] = useState('');
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<Map<string, StrategyResult>>(new Map());
  const [summary, setSummary] = useState<ComparisonSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Guide state
  const [guideOpen, setGuideOpen] = useState(true);
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set());
  const [activeGuideTab, setActiveGuideTab] = useState<'catalog' | 'picker'>('picker');

  useEffect(() => {
    playgroundApi.listStrategyOptions().then(setStrategies).catch(() => {});
    playgroundApi.listStrategyDetails().then(setDetails).catch(() => {});
    adminApi.listAvailableModels().then((models) => {
      setAvailableModels(models);
      if (models.length > 0 && !model) {
        setModel(models[0].id);
      }
    }).catch(() => {});
  }, []);

  function toggleStrategy(name: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleGuideCard(id: string) {
    setExpandedCards((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectStrategiesFromPicker(ids: string[]) {
    setSelected(new Set(ids));
  }

  function useExamplePrompt(text: string) {
    setPrompt(text);
  }

  async function runComparison() {
    if (!prompt.trim() || selected.size === 0 || running) return;

    setRunning(true);
    setResults(new Map());
    setSummary(null);
    setError(null);

    try {
      const selectedModelInfo = availableModels.find((m) => m.id === model);
      const resp = await authFetch(`${BASE}/strategies/compare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: prompt,
          model,
          system_prompt: systemPrompt,
          strategies: Array.from(selected),
          api_base: selectedModelInfo?.api_base || null,
        }),
      });

      if (!resp.ok) {
        let detail = `Server returned ${resp.status}`;
        try {
          const body = await resp.json();
          if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        } catch { /* no json body */ }
        setError(detail);
        setRunning(false);
        return;
      }

      for await (const evt of readSSE(resp)) {
        if (evt.event === 'strategy_result') {
          try {
            const data = JSON.parse(evt.data) as StrategyResult;
            setResults((prev) => new Map(prev).set(data.strategy, data));
          } catch {
            // ignore
          }
        } else if (evt.event === 'comparison_finished') {
          try {
            setSummary(JSON.parse(evt.data) as ComparisonSummary);
          } catch {
            // ignore
          }
        }
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to connect to the backend. Make sure the server is running.');
    } finally {
      setRunning(false);
    }
  }

  // Group details by category for the catalog
  const grouped = details.reduce<Record<string, StrategyDetail[]>>((acc, d) => {
    (acc[d.category] ??= []).push(d);
    return acc;
  }, {});

  const detailNameMap = Object.fromEntries(details.map((d) => [d.id, d.name]));

  return (
    <div className="flex flex-col gap-5">
      {/* ============================================================= */}
      {/* Strategy Guide                                                 */}
      {/* ============================================================= */}
      <Card className="overflow-hidden">
        <button
          onClick={() => setGuideOpen(!guideOpen)}
          className="w-full flex items-center justify-between px-1 py-0.5 text-left"
        >
          <div className="flex items-center gap-2">
            <Brain size={18} className="text-primary" />
            <span className="font-semibold text-sm">Strategy Guide</span>
            <span className="text-[12px] text-text-muted">
              &mdash; Learn which strategy fits your task
            </span>
          </div>
          <span className="text-text-muted">
            {guideOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </span>
        </button>

        {guideOpen && (
          <div className="mt-3">
            {/* Tab switcher */}
            <div className="flex gap-0 border-b border-border mb-4">
              <button
                onClick={() => setActiveGuideTab('picker')}
                className={`px-4 py-2 text-[13px] font-medium border-b-2 transition-colors ${
                  activeGuideTab === 'picker'
                    ? 'border-primary text-primary'
                    : 'border-transparent text-text-muted hover:text-text-secondary'
                }`}
              >
                Help Me Choose
              </button>
              <button
                onClick={() => setActiveGuideTab('catalog')}
                className={`px-4 py-2 text-[13px] font-medium border-b-2 transition-colors ${
                  activeGuideTab === 'catalog'
                    ? 'border-primary text-primary'
                    : 'border-transparent text-text-muted hover:text-text-secondary'
                }`}
              >
                All Strategies ({details.length})
              </button>
            </div>

            {/* Help Me Choose tab */}
            {activeGuideTab === 'picker' && details.length > 0 && (
              <div className="space-y-4">
                <p className="text-[13px] text-text-secondary leading-relaxed">
                  Not sure which strategy to use? Describe what you need and we&apos;ll suggest the
                  best options. Click <strong>Select</strong> to pre-fill the comparison form, then
                  run the same prompt through each to see which works best for your use case.
                </p>
                <DecisionHelper details={details} onSelect={selectStrategiesFromPicker} />

                {/* Quick tips */}
                <div className="bg-bg-subtle rounded-lg p-4">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Lightbulb size={14} className="text-amber-500" />
                    <span className="text-[12px] font-semibold text-text-primary">General Prompting Tips</span>
                  </div>
                  <ul className="space-y-1.5 text-[13px] text-text-secondary">
                    <li className="flex gap-2"><span className="text-text-muted">&bull;</span>
                      <span><strong>Be specific about the output format</strong> you want — strategies that evaluate or vote on outputs work better when they can objectively compare answers.</span>
                    </li>
                    <li className="flex gap-2"><span className="text-text-muted">&bull;</span>
                      <span><strong>Include quality criteria</strong> in your system prompt — reflection-based strategies (Reflexion, Evaluator-Optimizer) use these criteria to score and improve outputs.</span>
                    </li>
                    <li className="flex gap-2"><span className="text-text-muted">&bull;</span>
                      <span><strong>Start with Chain of Thought</strong> for new tasks — it&apos;s the cheapest and fastest way to baseline quality before trying more expensive strategies.</span>
                    </li>
                    <li className="flex gap-2"><span className="text-text-muted">&bull;</span>
                      <span><strong>Use the Strategy Lab to compare</strong> — run 2-3 candidate strategies with the same prompt to find the best quality/cost tradeoff for your specific task.</span>
                    </li>
                  </ul>
                </div>
              </div>
            )}

            {/* Full Catalog tab */}
            {activeGuideTab === 'catalog' && (
              <div className="space-y-4">
                {Object.entries(grouped).map(([category, items]) => (
                  <div key={category}>
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-[11px] px-2 py-0.5 rounded border font-medium uppercase tracking-wide ${
                        CATEGORY_COLORS[category] || 'bg-bg-subtle text-text-muted border-border'
                      }`}>
                        {category}
                      </span>
                      <span className="text-[12px] text-text-muted">
                        {category === 'reasoning' && '— Single-pass or iterative thinking'}
                        {category === 'search' && '— Explore many solution paths in parallel'}
                        {category === 'reflection' && '— Generate, critique, and improve'}
                        {category === 'consensus' && '— Multiple perspectives, one answer'}
                        {category === 'planning' && '— Plan steps, then execute'}
                      </span>
                    </div>
                    <div className="space-y-2">
                      {items.map((d) => (
                        <StrategyGuideCard
                          key={d.id}
                          detail={d}
                          isExpanded={expandedCards.has(d.id)}
                          onToggle={() => toggleGuideCard(d.id)}
                          onUseExample={useExamplePrompt}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>

      {/* ============================================================= */}
      {/* Comparison Config                                              */}
      {/* ============================================================= */}
      <Card className="flex flex-col gap-4">
        <div className="flex gap-4 flex-wrap items-end">
          {/* Model */}
          <label className="text-[13px] text-text-secondary flex-[0_0_180px]">
            Model
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface"
            >
              {availableModels.length === 0 && (
                <option value="">No models configured</option>
              )}
              {Object.entries(
                availableModels.reduce<Record<string, AvailableModel[]>>((acc, m) => {
                  (acc[m.provider] ??= []).push(m);
                  return acc;
                }, {}),
              ).map(([provider, models]) => (
                <optgroup key={provider} label={provider.charAt(0).toUpperCase() + provider.slice(1)}>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>{m.id}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>

          {/* System prompt */}
          <label className="text-[13px] text-text-secondary flex-1 min-w-[200px]">
            System Prompt (optional)
            <input
              type="text"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="You are a helpful assistant..."
              className="block w-full mt-1 px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface box-border"
            />
          </label>
        </div>

        {/* Strategies */}
        <div>
          <div className="text-[13px] text-text-secondary mb-2">Strategies</div>
          <div className="flex gap-2 flex-wrap">
            {strategies.map((s) => {
              const detail = details.find((d) => d.id === s);
              const catColor = CATEGORY_COLORS[detail?.category || ''];
              return (
                <label
                  key={s}
                  title={detail?.description}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[13px] cursor-pointer select-none transition-colors ${
                    selected.has(s)
                      ? 'border-2 border-primary bg-primary-light'
                      : 'border border-border bg-bg-surface hover:bg-bg-subtle'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(s)}
                    onChange={() => toggleStrategy(s)}
                    className="hidden"
                  />
                  {detail?.category && (
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                      catColor ? catColor.split(' ')[0].replace('/10', '') : 'bg-text-muted'
                    }`} />
                  )}
                  {detailNameMap[s] || s}
                </label>
              );
            })}
          </div>
        </div>

        {/* Prompt + run */}
        <div className="flex gap-3">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Enter a prompt to compare across strategies..."
            rows={3}
            className="flex-1 px-3 py-[9px] rounded-md border border-border text-sm font-[inherit] resize-y box-border bg-bg-surface"
          />
          <Button
            onClick={runComparison}
            disabled={running || !prompt.trim() || selected.size === 0}
            className="self-end whitespace-nowrap"
          >
            {running ? 'Running...' : 'Run Comparison'}
          </Button>
        </div>
      </Card>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm">
          {error}
        </div>
      )}

      {/* ============================================================= */}
      {/* Results Grid                                                   */}
      {/* ============================================================= */}
      {results.size > 0 && (
        <div
          className="grid gap-md"
          style={{ gridTemplateColumns: `repeat(${Math.min(results.size, 4)}, 1fr)` }}
        >
          {Array.from(results.values()).map((r) => (
            <Card
              key={r.strategy}
              className={`flex flex-col gap-3 ${r.status === 'error' ? 'border-error' : ''}`}
            >
              {/* Header */}
              <div className="flex items-center justify-between">
                <span className="font-semibold text-sm">
                  {detailNameMap[r.strategy] || r.strategy}
                </span>
                <Badge variant={r.status === 'completed' ? 'success' : 'error'}>
                  {r.status}
                </Badge>
              </div>

              {/* Output */}
              <div className="text-[13px] leading-relaxed whitespace-pre-wrap max-h-[200px] overflow-auto bg-bg-subtle p-2.5 rounded-md">
                {r.error || r.output || '(no output)'}
              </div>

              {/* Stats */}
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-text-muted">
                <span>Duration</span>
                <span className="font-medium text-text-primary">{r.duration_ms}ms</span>
                <span>Tokens</span>
                <span className="font-medium text-text-primary">{r.total_tokens}</span>
                <span>Cost</span>
                <span className="font-medium text-text-primary">${r.cost_usd.toFixed(4)}</span>
                <span>Steps</span>
                <span className="font-medium text-text-primary">{r.steps}</span>
                <span>Tool calls</span>
                <span className="font-medium text-text-primary">{r.tool_calls}</span>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Summary bar */}
      {summary && (
        <div className="bg-success-light rounded-lg border border-success/30 px-5 py-3 flex gap-6 text-sm text-success">
          <span>Compared: <strong>{summary.total_strategies}</strong> strategies</span>
          {summary.fastest && (
            <span>Fastest: <strong>{detailNameMap[summary.fastest] || summary.fastest}</strong></span>
          )}
          {summary.cheapest && (
            <span>Cheapest: <strong>{detailNameMap[summary.cheapest] || summary.cheapest}</strong></span>
          )}
        </div>
      )}
    </div>
  );
}
