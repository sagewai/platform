'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { EvalDatasetSummary, EvalRunDetail, AgentSummary, AvailableModel } from '@/utils/types';
import { Card, Button, Skeleton } from '@/components/ui/legacy';
import { Play, CheckCircle2, XCircle } from 'lucide-react';
import { HelpPanel } from '@/components/help-panel';

export default function RunEvalPage() {
  const [datasets, setDatasets] = useState<EvalDatasetSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedDataset, setSelectedDataset] = useState('');
  const [selectedAgent, setSelectedAgent] = useState('');
  const [judgeModel, setJudgeModel] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<EvalRunDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [ds, ag, models] = await Promise.all([
        adminApi.listEvalDatasets(),
        adminApi.listAgents(),
        adminApi.listAvailableModels(),
      ]);
      setDatasets(ds);
      setAgents(ag);
      setAvailableModels(models);
      if (ds.length > 0) setSelectedDataset(String(ds[0].id));
      if (ag.length > 0) setSelectedAgent(ag[0].name);
      if (models.length > 0) setJudgeModel(models[0].id);
      setError(null);
    } catch {
      setError('Failed to load data. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedDataset || !selectedAgent) return;
    setRunning(true);
    setResult(null);
    setRunError(null);
    try {
      const data = await adminApi.runEval(
        Number(selectedDataset),
        selectedAgent,
        judgeModel,
      );
      setResult(data);
    } catch (err: unknown) {
      // Surface the backend's real reason (e.g. a 501 "Evaluation requires a
      // running agent with LLM keys") rather than blaming the user's inputs.
      setRunError(
        err instanceof Error && err.message
          ? err.message
          : 'Eval run failed.',
      );
    } finally {
      setRunning(false);
    }
  }

  const rawRate = result?.summary?.pass_rate ?? result?.pass_rate ?? 0;
  const passRatePct = Number.isFinite(rawRate) ? Math.round(rawRate * 100) : 0;

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">
        Run Evaluation
      </h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Select a dataset and agent to run a scored evaluation. Results are judged by an LLM.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      <Card className="mb-lg">
        {loading ? (
          <Skeleton lines={4} />
        ) : (
          <form onSubmit={handleRun} className="space-y-4">
            <div>
              <label htmlFor="dataset-select" className="block text-xs font-semibold text-text-secondary mb-1">
                Dataset
              </label>
              {datasets.length === 0 ? (
                <p className="text-sm text-text-muted">
                  No datasets found.{' '}
                  <a href="/eval/datasets" className="text-primary underline">Create one first.</a>
                </p>
              ) : (
                <select
                  id="dataset-select"
                  value={selectedDataset}
                  onChange={(e) => setSelectedDataset(e.target.value)}
                  className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
                >
                  {datasets.map((ds) => (
                    <option key={ds.id} value={String(ds.id)}>
                      {ds.name} ({ds.case_count} case{ds.case_count !== 1 ? 's' : ''})
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div>
              <label htmlFor="agent-select" className="block text-xs font-semibold text-text-secondary mb-1">
                Agent
              </label>
              {agents.length === 0 ? (
                <p className="text-sm text-text-muted">No agents registered.</p>
              ) : (
                <select
                  id="agent-select"
                  value={selectedAgent}
                  onChange={(e) => setSelectedAgent(e.target.value)}
                  className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
                >
                  {agents.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div>
              <label htmlFor="judge-select" className="block text-xs font-semibold text-text-secondary mb-1">
                Judge Model
              </label>
              <select
                id="judge-select"
                value={judgeModel}
                onChange={(e) => setJudgeModel(e.target.value)}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
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
            </div>

            {runError && (
              <p className="text-error text-sm">{runError}</p>
            )}

            <Button
              type="submit"
              disabled={running || !selectedDataset || !selectedAgent}
            >
              <Play size={14} className="mr-1.5" aria-hidden="true" />
              {running ? 'Running…' : 'Run Eval'}
            </Button>
          </form>
        )}
      </Card>

      {/* Progress / result */}
      {running && (
        <Card>
          <div className="flex items-center gap-3 text-sm text-text-secondary py-4 justify-center">
            <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" aria-hidden="true" />
            Running evaluation — this may take a moment…
          </div>
        </Card>
      )}

      {result && (
        <Card>
          <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
            Results — Run #{result.id}
          </h3>

          {/* Summary stats */}
          <div className="grid grid-cols-4 gap-3 mb-lg">
            {[
              { label: 'Total', value: result.summary.total },
              { label: 'Passed', value: result.summary.passed },
              { label: 'Failed', value: result.summary.failed },
              { label: 'Pass Rate', value: `${passRatePct}%` },
            ].map(({ label, value }) => (
              <div key={label} className="p-3 rounded-md border border-border text-center">
                <div className="text-xl font-bold">{value}</div>
                <div className="text-xs text-text-muted mt-0.5">{label}</div>
              </div>
            ))}
          </div>

          {/* Per-case scores */}
          <p className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
            Per-Case Scores
          </p>
          <div className="space-y-2">
            {result.scores.map((s, i) => (
              <div
                key={i}
                className="flex items-start gap-3 p-3 rounded-md border border-border text-sm"
              >
                {s.passed ? (
                  <CheckCircle2 size={16} className="shrink-0 text-success mt-0.5" aria-label="Passed" />
                ) : (
                  <XCircle size={16} className="shrink-0 text-error mt-0.5" aria-label="Failed" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium">Case #{i + 1}</span>
                    <span className="text-xs text-text-muted">score: {s.score.toFixed(2)}</span>
                  </div>
                  <p className="text-[13px] text-text-secondary mb-1">{s.reasoning}</p>
                  {Object.keys(s.criteria_scores).length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(s.criteria_scores).map(([k, v]) => (
                        <span key={k} className="text-xs bg-bg-surface border border-border rounded px-2 py-0.5">
                          {k}: {v.toFixed(2)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <HelpPanel title="Evaluation">
        <h3>Judge Models</h3>
        <p>The judge model scores each test case against your criteria. <code>gpt-4o-mini</code> is fast and cheap; <code>gpt-4o</code> gives more nuanced scoring.</p>
        <h3>Scoring Criteria</h3>
        <p>Each dataset case defines criteria (e.g. accuracy, helpfulness). The judge assigns 0-1 scores per criterion and an overall pass/fail.</p>
        <h3>Pass Rate</h3>
        <p>A case passes when its overall score meets the threshold (default 0.5). The pass rate is the fraction of cases that pass.</p>
        <h3>Tips</h3>
        <ul>
          <li>Start with a small dataset (5-10 cases) to validate your criteria</li>
          <li>Include both expected-pass and expected-fail cases</li>
          <li>Use <code>expected_output</code> to give the judge a reference answer</li>
        </ul>
      </HelpPanel>
    </div>
  );
}
