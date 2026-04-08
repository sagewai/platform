'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { EvalRunSummary, EvalRunDetail } from '@/utils/types';
import { Card, Button, Skeleton } from '@sagecurator/ui';
import { BarChart2, CheckCircle2, XCircle, ChevronDown, ChevronRight } from 'lucide-react';

export default function EvalReportsPage() {
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Detail drill-in
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const fetchRuns = useCallback(async () => {
    try {
      const data = await adminApi.listEvalRuns();
      setRuns(data);
      setError(null);
    } catch {
      setError('Failed to load eval runs. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  async function handleToggle(id: number) {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    setDetail(null);
    setLoadingDetail(true);
    try {
      const data = await adminApi.getEvalRun(id);
      setDetail(data);
    } catch {
      setError('Failed to load run detail');
    } finally {
      setLoadingDetail(false);
    }
  }

  function passRateColor(rate: number): string {
    if (rate >= 0.8) return 'text-success';
    if (rate >= 0.5) return 'text-warning';
    return 'text-error';
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-start justify-between mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
            Eval Reports
          </h1>
          <p className="mt-0 mb-0 text-sm text-text-secondary">
            Historical evaluation runs with per-case scoring and pass/fail breakdown.
          </p>
        </div>
        <Button variant="secondary" onClick={fetchRuns}>
          Refresh
        </Button>
      </div>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {loading ? (
        <Skeleton lines={4} />
      ) : runs.length === 0 ? (
        <Card>
          <div className="text-center py-10">
            <BarChart2 size={32} className="mx-auto mb-3 text-text-muted" aria-hidden="true" />
            <p className="text-sm text-text-secondary">No eval runs yet.</p>
            <p className="text-xs text-text-muted mt-1">
              Go to{' '}
              <a href="/eval/run" className="text-primary underline">Run Eval</a>{' '}
              to execute your first evaluation.
            </p>
          </div>
        </Card>
      ) : (
        <div className="space-y-2">
          {runs.map((run) => {
            const pct = Math.round(run.pass_rate * 100);
            return (
              <Card key={run.id} className="p-0 overflow-hidden">
                {/* Summary row */}
                <button
                  onClick={() => handleToggle(run.id)}
                  className="w-full flex items-center gap-3 px-5 py-3.5 text-left"
                  aria-expanded={expandedId === run.id}
                >
                  {expandedId === run.id ? (
                    <ChevronDown size={14} className="shrink-0 text-text-muted" aria-hidden="true" />
                  ) : (
                    <ChevronRight size={14} className="shrink-0 text-text-muted" aria-hidden="true" />
                  )}

                  <span className="font-semibold text-sm">Run #{run.id}</span>
                  <span className="text-xs text-text-muted">{run.agent_name}</span>
                  <span className="text-xs text-text-muted hidden sm:block">dataset #{run.dataset_id}</span>
                  <span className="text-xs text-text-muted hidden md:block">{run.model}</span>

                  <div className="ml-auto flex items-center gap-4 shrink-0">
                    <span className={`text-sm font-bold ${passRateColor(run.pass_rate)}`}>
                      {pct}%
                    </span>
                    <span className="text-xs text-text-muted">
                      {run.passed}/{run.total_cases} passed
                    </span>
                    {run.created_at && (
                      <span className="text-xs text-text-muted hidden lg:block">
                        {new Date(run.created_at).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                </button>

                {/* Detail panel */}
                {expandedId === run.id && (
                  <div className="border-t border-border px-5 py-4">
                    {loadingDetail ? (
                      <Skeleton lines={3} />
                    ) : detail ? (
                      <>
                        {/* Stats bar */}
                        <div className="grid grid-cols-4 gap-3 mb-lg">
                          {[
                            { label: 'Total', value: detail.summary.total },
                            { label: 'Passed', value: detail.summary.passed },
                            { label: 'Failed', value: detail.summary.failed },
                            { label: 'Avg Score', value: detail.summary.avg_score.toFixed(2) },
                          ].map(({ label, value }) => (
                            <div key={label} className="p-3 rounded-md border border-border text-center">
                              <div className="text-lg font-bold">{value}</div>
                              <div className="text-xs text-text-muted mt-0.5">{label}</div>
                            </div>
                          ))}
                        </div>

                        {/* Per-case */}
                        <p className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
                          Per-Case Results
                        </p>
                        <div className="space-y-2">
                          {detail.scores.map((s, i) => (
                            <div
                              key={i}
                              className="flex items-start gap-3 p-3 rounded-md border border-border text-sm"
                            >
                              {s.passed ? (
                                <CheckCircle2
                                  size={16}
                                  className="shrink-0 text-success mt-0.5"
                                  aria-label="Passed"
                                />
                              ) : (
                                <XCircle
                                  size={16}
                                  className="shrink-0 text-error mt-0.5"
                                  aria-label="Failed"
                                />
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
                                      <span
                                        key={k}
                                        className="text-xs bg-bg-surface border border-border rounded px-2 py-0.5"
                                      >
                                        {k}: {v.toFixed(2)}
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </>
                    ) : null}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
