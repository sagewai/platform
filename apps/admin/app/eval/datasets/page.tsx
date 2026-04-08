'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { EvalDatasetSummary, EvalDatasetDetail, EvalCaseData } from '@/utils/types';
import { Card, Button, Skeleton } from '@sagecurator/ui';
import { Database, Plus, Trash2, ChevronDown, ChevronRight } from 'lucide-react';

export default function EvalDatasetsPage() {
  const [datasets, setDatasets] = useState<EvalDatasetSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create dataset form
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [newCasesJson, setNewCasesJson] = useState(
    JSON.stringify(
      [
        {
          input: 'What is the capital of France?',
          agent_name: 'my-agent',
          criteria: ['correct answer', 'concise'],
          expected_output: 'Paris',
          metadata: null,
        },
      ],
      null,
      2,
    ),
  );
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // Detail view
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<EvalDatasetDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Deleting
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const fetchDatasets = useCallback(async () => {
    try {
      const data = await adminApi.listEvalDatasets();
      setDatasets(data);
      setError(null);
    } catch {
      setError('Failed to load datasets. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDatasets();
  }, [fetchDatasets]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreateError(null);
    let cases: EvalCaseData[];
    try {
      cases = JSON.parse(newCasesJson);
      if (!Array.isArray(cases)) throw new Error('Cases must be an array');
    } catch (err) {
      setCreateError(`Invalid JSON: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    setCreating(true);
    try {
      await adminApi.createEvalDataset(newName.trim(), cases, newDescription.trim() || undefined);
      setShowCreate(false);
      setNewName('');
      setNewDescription('');
      setCreating(false);
      fetchDatasets();
    } catch {
      setCreateError('Failed to create dataset');
      setCreating(false);
    }
  }

  async function handleToggleDetail(id: number) {
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    setDetail(null);
    setLoadingDetail(true);
    try {
      const data = await adminApi.getEvalDataset(id);
      setDetail(data);
    } catch {
      setError('Failed to load dataset detail');
    } finally {
      setLoadingDetail(false);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm('Delete this dataset? This cannot be undone.')) return;
    setDeletingId(id);
    try {
      await adminApi.deleteEvalDataset(id);
      setDatasets((prev) => prev.filter((d) => d.id !== id));
      if (expandedId === id) {
        setExpandedId(null);
        setDetail(null);
      }
    } catch {
      setError('Failed to delete dataset');
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-start justify-between mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
            Eval Datasets
          </h1>
          <p className="mt-0 mb-0 text-sm text-text-secondary">
            Manage evaluation datasets — collections of test cases used to benchmark agents.
          </p>
        </div>
        <Button onClick={() => setShowCreate((s) => !s)}>
          <Plus size={14} className="mr-1.5" aria-hidden="true" />
          New Dataset
        </Button>
      </div>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Create form */}
      {showCreate && (
        <Card className="mb-lg border border-primary/30">
          <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
            New Dataset
          </h3>
          <form onSubmit={handleCreate} className="space-y-3">
            <div>
              <label className="block text-xs font-semibold text-text-secondary mb-1">
                Name <span className="text-error">*</span>
              </label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                required
                placeholder="e.g. customer-support-qa-v1"
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-text-secondary mb-1">
                Description
              </label>
              <input
                type="text"
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                placeholder="Optional description"
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-text-secondary mb-1">
                Cases (JSON array)
              </label>
              <textarea
                value={newCasesJson}
                onChange={(e) => setNewCasesJson(e.target.value)}
                rows={10}
                className="w-full px-3 py-2.5 border border-border rounded-md text-xs font-mono resize-y box-border bg-bg-surface"
              />
            </div>
            {createError && (
              <p className="text-error text-xs">{createError}</p>
            )}
            <div className="flex gap-2">
              <Button type="submit" disabled={creating || !newName.trim()}>
                {creating ? 'Creating...' : 'Create'}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => { setShowCreate(false); setCreateError(null); }}
              >
                Cancel
              </Button>
            </div>
          </form>
        </Card>
      )}

      {/* Dataset list */}
      {loading ? (
        <Skeleton lines={4} />
      ) : datasets.length === 0 ? (
        <Card>
          <div className="text-center py-10">
            <Database size={32} className="mx-auto mb-3 text-text-muted" aria-hidden="true" />
            <p className="text-sm text-text-secondary">No datasets yet.</p>
            <p className="text-xs text-text-muted mt-1">
              Click &quot;New Dataset&quot; to create your first evaluation dataset.
            </p>
          </div>
        </Card>
      ) : (
        <div className="space-y-2">
          {datasets.map((ds) => (
            <Card key={ds.id} className="p-0 overflow-hidden">
              {/* Header row */}
              <div className="flex items-center gap-3 px-5 py-3.5">
                <button
                  onClick={() => handleToggleDetail(ds.id)}
                  className="flex items-center gap-2 flex-1 min-w-0 text-left"
                  aria-expanded={expandedId === ds.id}
                >
                  {expandedId === ds.id ? (
                    <ChevronDown size={14} className="shrink-0 text-text-muted" aria-hidden="true" />
                  ) : (
                    <ChevronRight size={14} className="shrink-0 text-text-muted" aria-hidden="true" />
                  )}
                  <span className="font-semibold text-sm truncate">{ds.name}</span>
                  {ds.description && (
                    <span className="text-xs text-text-muted truncate hidden sm:block">
                      — {ds.description}
                    </span>
                  )}
                </button>

                <div className="flex items-center gap-4 shrink-0">
                  <span className="text-xs text-text-muted">{ds.case_count} case{ds.case_count !== 1 ? 's' : ''}</span>
                  {ds.created_at && (
                    <span className="text-xs text-text-muted hidden md:block">
                      {new Date(ds.created_at).toLocaleDateString()}
                    </span>
                  )}
                  <button
                    onClick={() => handleDelete(ds.id)}
                    disabled={deletingId === ds.id}
                    aria-label={`Delete dataset ${ds.name}`}
                    className="text-text-muted hover:text-error transition-colors disabled:opacity-50"
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </div>
              </div>

              {/* Detail panel */}
              {expandedId === ds.id && (
                <div className="border-t border-border px-5 py-4">
                  {loadingDetail ? (
                    <Skeleton lines={3} />
                  ) : detail ? (
                    <div>
                      <p className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">
                        Cases
                      </p>
                      <div className="space-y-3">
                        {detail.cases.map((c, i) => (
                          <div
                            key={i}
                            className="p-3 rounded-md border border-border bg-bg-surface text-sm"
                          >
                            <div className="flex items-center gap-2 mb-1.5">
                              <span className="text-xs text-text-muted font-mono">#{i + 1}</span>
                              <span className="text-xs font-semibold text-text-secondary">{c.agent_name}</span>
                            </div>
                            <p className="mb-1 text-[13px]"><span className="font-medium">Input:</span> {c.input}</p>
                            {c.expected_output && (
                              <p className="mb-1 text-[13px]"><span className="font-medium">Expected:</span> {c.expected_output}</p>
                            )}
                            {c.criteria.length > 0 && (
                              <p className="text-[13px]">
                                <span className="font-medium">Criteria:</span>{' '}
                                {c.criteria.join(' · ')}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
