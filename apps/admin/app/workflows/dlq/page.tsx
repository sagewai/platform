'use client';

import { useEffect, useState, useCallback } from 'react';
import { Card, Badge, EmptyState, ConfirmDialog } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type { DLQEntry } from '@/utils/types';
import { AlertTriangle, HelpCircle, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';

export default function DLQPage() {
  const [entries, setEntries] = useState<DLQEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [discardTarget, setDiscardTarget] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);

  const fetchEntries = useCallback(async () => {
    try {
      const data = await adminApi.listDLQ({ limit: 50 });
      setEntries(data);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEntries();
  }, [fetchEntries]);

  const handleRetry = async (runId: string) => {
    try {
      const result = await adminApi.retryDLQ(runId);
      alert(`Retried as: ${result.new_run_id}`);
      fetchEntries();
    } catch (e: unknown) {
      alert(`Retry failed: ${e instanceof Error ? e.message : 'Unknown error'}`);
    }
  };

  const handleDiscard = async (runId: string) => {
    try {
      await adminApi.discardDLQ(runId);
      setDiscardTarget(null);
      fetchEntries();
    } catch (e: unknown) {
      alert(`Discard failed: ${e instanceof Error ? e.message : 'Unknown error'}`);
    }
  };

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-lg">
        <div>
          <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">
            Failed Workflows
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Workflows that failed after exhausting retries — review, retry, or discard
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowHelp(!showHelp)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border hover:bg-bg-subtle"
            title="Help"
          >
            <HelpCircle className="w-3.5 h-3.5" />
            Help
          </button>
          <button
            onClick={fetchEntries}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border hover:bg-bg-subtle"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </div>

      {showHelp && (
        <Card>
          <div className="p-md text-sm text-text-secondary space-y-2">
            <p>Workflows land here after exhausting their retry attempts (Dead Letter Queue). Review the error, fix the underlying cause, then retry. You can also discard entries that are no longer relevant.</p>
          </div>
        </Card>
      )}

      {loading ? (
        <Card><p className="text-text-muted p-md text-sm">Loading...</p></Card>
      ) : entries.length === 0 ? (
        <EmptyState
          icon={<AlertTriangle className="w-10 h-10" />}
          title="No failed workflows"
          description="All workflows completed successfully or were discarded"
        />
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Run ID</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Workflow</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Error</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Retries</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Created</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Actions</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-b border-border last:border-0 hover:bg-bg-subtle">
                  <td className="py-2.5 px-3 font-[family-name:var(--font-mono)] text-[13px]">
                    {e.run_id}
                  </td>
                  <td className="py-2.5 px-3">{e.workflow_name}</td>
                  <td className="py-2.5 px-3 text-text-secondary max-w-[200px] truncate" title={e.error}>
                    {e.error}
                  </td>
                  <td className="py-2.5 px-3">
                    <Badge variant="default">{e.retry_count}</Badge>
                  </td>
                  <td className="py-2.5 px-3 text-text-secondary text-[13px]">
                    {new Date(e.created_at).toLocaleString()}
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleRetry(e.run_id)}
                        className="flex items-center gap-1 px-2 py-1 text-xs rounded border border-border hover:bg-bg-subtle"
                        title="Retry"
                      >
                        <RotateCcw className="w-3 h-3" /> Retry
                      </button>
                      <button
                        onClick={() => setDiscardTarget(e.run_id)}
                        className="flex items-center gap-1 px-2 py-1 text-xs rounded border border-error/30 text-error hover:bg-error/10"
                        title="Discard"
                      >
                        <Trash2 className="w-3 h-3" /> Discard
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {discardTarget && (
        <ConfirmDialog
          open={!!discardTarget}
          onClose={() => setDiscardTarget(null)}
          title="Discard Failed Workflow"
          message={`Permanently remove ${discardTarget} from the failed workflows list?`}
          confirmLabel="Discard"
          onConfirm={() => handleDiscard(discardTarget)}
        />
      )}
    </div>
  );
}
