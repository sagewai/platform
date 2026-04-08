'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { Card, Button, Badge, Skeleton, EmptyState, ConfirmDialog, useToast } from '@sagecurator/ui';
import { Trash2, Download } from 'lucide-react';
import { authFetch } from '@/utils/auth';

const API_URL = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

interface OllamaStatus {
  status: string;
  version: string | null;
  base_url: string;
}

interface OllamaModel {
  name: string;
  size: number;
  modified_at: string;
  digest: string;
}

interface PullProgress {
  status: string;
  completed?: number;
  total?: number;
}

function formatSize(bytes: number): string {
  if (bytes === 0) return '—';
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

export default function OllamaPage() {
  const [status, setStatus] = useState<OllamaStatus | null>(null);
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const [pullName, setPullName] = useState('');
  const [pulling, setPulling] = useState(false);
  const [pullProgress, setPullProgress] = useState<PullProgress | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [statusRes, modelsRes] = await Promise.all([
        authFetch(`${API_URL}/api/v1/ollama/status`).then((r) => r.json()),
        authFetch(`${API_URL}/api/v1/ollama/models`).then((r) => {
          if (!r.ok) return [];
          return r.json();
        }),
      ]);
      setStatus(statusRes);
      setModels(modelsRes);
      setError(null);
    } catch {
      setError('Failed to reach admin backend.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function handlePull() {
    if (!pullName.trim()) return;
    setPulling(true);
    setPullProgress(null);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const resp = await authFetch(`${API_URL}/api/v1/ollama/pull`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_name: pullName.trim() }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        throw new Error('Pull request failed');
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const progress = JSON.parse(line) as PullProgress;
            setPullProgress(progress);
          } catch {
            // skip unparseable lines
          }
        }
      }
      toast('success', `Model "${pullName}" pulled successfully`);
      setPullName('');
      await fetchData();
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        toast('error', 'Pull failed');
      }
    } finally {
      setPulling(false);
      setPullProgress(null);
      abortRef.current = null;
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      const resp = await authFetch(`${API_URL}/api/v1/ollama/models/${encodeURIComponent(deleteTarget)}`, {
        method: 'DELETE',
      });
      if (!resp.ok) throw new Error('Delete failed');
      toast('success', `Deleted "${deleteTarget}"`);
      setDeleteTarget(null);
      await fetchData();
    } catch {
      toast('error', 'Failed to delete model');
    }
  }

  const progressPct =
    pullProgress?.completed && pullProgress?.total
      ? Math.round((pullProgress.completed / pullProgress.total) * 100)
      : null;

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Ollama</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Manage local Ollama models — view installed models, pull new ones, and monitor status.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md" role="alert">
          {error}
        </div>
      )}

      {/* Status */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Status</h3>
        {loading ? (
          <Skeleton lines={2} />
        ) : status ? (
          <div className="flex flex-wrap gap-6 text-sm">
            <div>
              <span className="text-text-muted text-xs block mb-0.5">Connection</span>
              <Badge variant={status.status === 'running' ? 'success' : 'error'}>
                {status.status}
              </Badge>
            </div>
            <div>
              <span className="text-text-muted text-xs block mb-0.5">Version</span>
              <span className="font-[family-name:var(--font-mono)] text-[13px]">{status.version ?? '—'}</span>
            </div>
            <div>
              <span className="text-text-muted text-xs block mb-0.5">Base URL</span>
              <span className="font-[family-name:var(--font-mono)] text-[13px]">{status.base_url}</span>
            </div>
          </div>
        ) : (
          <p className="text-sm text-text-muted">Unable to check Ollama status.</p>
        )}
      </Card>

      {/* Pull Model */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Pull Model</h3>
        <div className="flex gap-2 mb-3">
          <input
            type="text"
            value={pullName}
            onChange={(e) => setPullName(e.target.value)}
            placeholder="e.g. llama3.2, mistral, phi3"
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
            disabled={pulling}
          />
          <Button onClick={handlePull} disabled={pulling || !pullName.trim()}>
            <Download size={14} className="mr-1.5" aria-hidden="true" />
            {pulling ? 'Pulling...' : 'Pull'}
          </Button>
        </div>
        {pulling && pullProgress && (
          <div>
            <p className="text-xs text-text-muted mb-1.5">{pullProgress.status}</p>
            {progressPct !== null && (
              <div className="w-full h-2 bg-bg-subtle rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all duration-300"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            )}
            {progressPct !== null && (
              <p className="text-xs text-text-muted mt-1">{progressPct}%</p>
            )}
          </div>
        )}
      </Card>

      {/* Installed Models */}
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Installed Models</h3>
        {loading ? (
          <Skeleton lines={4} />
        ) : models.length === 0 ? (
          <EmptyState
            title="No models installed"
            description="Pull a model to get started with local inference."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse" role="table">
              <thead>
                <tr className="border-b-2 border-border">
                  <th scope="col" className="text-left py-2.5 px-3 text-xs text-text-muted font-semibold uppercase tracking-wide">Name</th>
                  <th scope="col" className="text-left py-2.5 px-3 text-xs text-text-muted font-semibold uppercase tracking-wide">Size</th>
                  <th scope="col" className="text-left py-2.5 px-3 text-xs text-text-muted font-semibold uppercase tracking-wide hidden sm:table-cell">Modified</th>
                  <th scope="col" className="text-right py-2.5 px-3 text-xs text-text-muted font-semibold uppercase tracking-wide">Actions</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.name} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                    <td className="py-2.5 px-3 font-medium font-[family-name:var(--font-mono)] text-[13px]">{m.name}</td>
                    <td className="py-2.5 px-3 text-text-muted">{formatSize(m.size)}</td>
                    <td className="py-2.5 px-3 text-text-muted text-xs hidden sm:table-cell">
                      {m.modified_at ? new Date(m.modified_at).toLocaleDateString() : '—'}
                    </td>
                    <td className="py-2.5 px-3 text-right">
                      <Button
                        size="sm"
                        variant="secondary"
                        className="text-error"
                        onClick={() => setDeleteTarget(m.name)}
                      >
                        <Trash2 size={12} className="mr-1" aria-hidden="true" />
                        Delete
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="Delete Model"
        message={`Are you sure you want to delete "${deleteTarget}"? You can re-pull it later.`}
      />
    </div>
  );
}
