'use client';

import { useState } from 'react';
import { Card, Button, TextArea, useToast } from '@/components/ui/legacy';
import { Brain, Database, Sparkles, BookOpen } from 'lucide-react';

interface AgentMemoryConfig {
  context_scopes: string[];
  retrieval_config: {
    top_k: number;
    strategies: string[];
    reranking: boolean;
  };
  directive_template: string;
  auto_learn: boolean;
}

interface Props {
  agentName: string;
  initialConfig?: Partial<AgentMemoryConfig>;
  onSave: (config: AgentMemoryConfig) => Promise<void>;
}

const ALL_SCOPES = ['org', 'project'] as const;
const ALL_STRATEGIES = ['vector', 'bm25', 'graph'] as const;

export function AgentMemoryConfigPanel({ agentName, initialConfig, onSave }: Props) {
  const [scopes, setScopes] = useState<string[]>(initialConfig?.context_scopes ?? ['project']);
  const [topK, setTopK] = useState(initialConfig?.retrieval_config?.top_k ?? 5);
  const [strategies, setStrategies] = useState<string[]>(initialConfig?.retrieval_config?.strategies ?? ['vector', 'bm25']);
  const [reranking, setReranking] = useState(initialConfig?.retrieval_config?.reranking ?? false);
  const [directiveTemplate, setDirectiveTemplate] = useState(initialConfig?.directive_template ?? '');
  const [autoLearn, setAutoLearn] = useState(initialConfig?.auto_learn ?? false);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();

  function toggleScope(scope: string) {
    setScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  }

  function toggleStrategy(strategy: string) {
    setStrategies((prev) =>
      prev.includes(strategy) ? prev.filter((s) => s !== strategy) : [...prev, strategy],
    );
  }

  async function handleSave() {
    setSaving(true);
    try {
      await onSave({
        context_scopes: scopes,
        retrieval_config: { top_k: topK, strategies, reranking },
        directive_template: directiveTemplate,
        auto_learn: autoLearn,
      });
      toast('success', 'Memory & context configuration saved');
    } catch (e) {
      toast('error', `Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-lg">
      {/* Context Scope Access */}
      <Card className="p-md">
        <div className="flex items-center gap-2 mb-md">
          <Database size={14} className="text-blue-400" />
          <h3 className="text-sm font-semibold">Context Scope Access</h3>
        </div>
        <p className="text-xs text-text-muted mb-md">
          Select which knowledge scopes this agent can access during retrieval.
        </p>
        <div className="flex flex-wrap gap-2">
          {ALL_SCOPES.map((scope) => {
            const active = scopes.includes(scope);
            return (
              <button
                key={scope}
                onClick={() => toggleScope(scope)}
                className={`px-3 py-1.5 rounded text-xs font-medium border transition-colors ${
                  active
                    ? 'bg-primary/20 border-primary/50 text-primary'
                    : 'bg-white/5 border-white/10 text-text-muted hover:border-white/20'
                }`}
              >
                {scope.charAt(0).toUpperCase() + scope.slice(1)}
              </button>
            );
          })}
        </div>
      </Card>

      {/* Retrieval Settings */}
      <Card className="p-md">
        <div className="flex items-center gap-2 mb-md">
          <Brain size={14} className="text-teal-400" />
          <h3 className="text-sm font-semibold">Retrieval Settings</h3>
        </div>

        <div className="space-y-md">
          <div className="flex items-center gap-4">
            <label className="text-xs text-text-muted w-20">Top-K</label>
            <input
              type="range"
              min={1}
              max={20}
              value={topK}
              onChange={(e) => setTopK(parseInt(e.target.value))}
              className="flex-1"
            />
            <span className="text-xs font-mono w-6 text-right">{topK}</span>
          </div>

          <div>
            <label className="text-xs text-text-muted block mb-2">Search Strategies</label>
            <div className="flex gap-2">
              {ALL_STRATEGIES.map((strategy) => {
                const active = strategies.includes(strategy);
                return (
                  <button
                    key={strategy}
                    onClick={() => toggleStrategy(strategy)}
                    className={`px-3 py-1.5 rounded text-xs font-medium border transition-colors ${
                      active
                        ? 'bg-primary/20 border-primary/50 text-primary'
                        : 'bg-white/5 border-white/10 text-text-muted hover:border-white/20'
                    }`}
                  >
                    {strategy === 'bm25' ? 'BM25' : strategy.charAt(0).toUpperCase() + strategy.slice(1)}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <label className="text-xs font-medium block">Cross-Encoder Re-ranking</label>
              <span className="text-[11px] text-text-muted">Apply re-ranking to improve result quality</span>
            </div>
            <button
              onClick={() => setReranking(!reranking)}
              className={`relative w-10 h-5 rounded-full transition-colors ${
                reranking ? 'bg-primary' : 'bg-white/20'
              }`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                  reranking ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>
        </div>
      </Card>

      {/* Directive Template */}
      <Card className="p-md">
        <div className="flex items-center gap-2 mb-md">
          <Sparkles size={14} className="text-purple-400" />
          <h3 className="text-sm font-semibold">Directive Template</h3>
        </div>
        <p className="text-xs text-text-muted mb-md">
          Define a directive template prepended to every prompt for this agent. Supports sigil syntax
          (<code className="text-primary">@context</code>, <code className="text-primary">@memory</code>,
          <code className="text-primary">#model</code>, etc.).
        </p>
        <TextArea
          value={directiveTemplate}
          onChange={(e) => setDirectiveTemplate(e.target.value)}
          placeholder={`Example:\n@context('relevant background for {{topic}}')\n@memory('previous interactions')\n#budget:0.25`}
          rows={5}
        />
      </Card>

      {/* Auto-Learn */}
      <Card className="p-md">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BookOpen size={14} className="text-amber-400" />
            <div>
              <h3 className="text-sm font-semibold">Auto-Learn</h3>
              <p className="text-[11px] text-text-muted">Automatically extract and store memories from agent conversations</p>
            </div>
          </div>
          <button
            onClick={() => setAutoLearn(!autoLearn)}
            className={`relative w-10 h-5 rounded-full transition-colors ${
              autoLearn ? 'bg-primary' : 'bg-white/20'
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                autoLearn ? 'translate-x-5' : ''
              }`}
            />
          </button>
        </div>
      </Card>

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save Configuration'}
        </Button>
      </div>
    </div>
  );
}
