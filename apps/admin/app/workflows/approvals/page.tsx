'use client';

import { useEffect, useState, useCallback } from 'react';
import { Card, Badge, Button, EmptyState, Dialog } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type { WorkflowRun } from '@/utils/types';
import { CheckCircle, HelpCircle, XCircle, Clock, RefreshCw } from 'lucide-react';

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [rejectTarget, setRejectTarget] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [showHelp, setShowHelp] = useState(false);

  const fetchApprovals = useCallback(async () => {
    try {
      const data = await adminApi.listApprovals();
      setApprovals(data);
    } catch {
      setApprovals([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchApprovals();
    const interval = setInterval(fetchApprovals, 5000);
    return () => clearInterval(interval);
  }, [fetchApprovals]);

  const handleApprove = async (runId: string) => {
    try {
      await adminApi.approveWorkflow(runId);
      fetchApprovals();
    } catch (e: unknown) {
      alert(`Approve failed: ${e instanceof Error ? e.message : 'Unknown error'}`);
    }
  };

  const handleReject = async () => {
    if (!rejectTarget) return;
    try {
      await adminApi.rejectWorkflow(rejectTarget, rejectReason);
      setRejectTarget(null);
      setRejectReason('');
      fetchApprovals();
    } catch (e: unknown) {
      alert(`Reject failed: ${e instanceof Error ? e.message : 'Unknown error'}`);
    }
  };

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-lg">
        <div>
          <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">
            Pending Approvals
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Workflows waiting for human review
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
            onClick={fetchApprovals}
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
            <p>Workflows using ApprovalGate pause here and wait for human review. Examine the agent&apos;s work so far, then approve to continue execution or reject to terminate the workflow with a reason.</p>
          </div>
        </Card>
      )}

      {loading ? (
        <Card><p className="text-text-muted p-md text-sm">Loading...</p></Card>
      ) : approvals.length === 0 ? (
        <EmptyState
          icon={<Clock className="w-10 h-10" />}
          title="No pending approvals"
          description="All workflows are either approved or not awaiting review"
        />
      ) : (
        <Card>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Run ID</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Workflow</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Status</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Waiting Since</th>
                <th className="py-2.5 px-3 text-left text-xs text-text-muted uppercase">Actions</th>
              </tr>
            </thead>
            <tbody>
              {approvals.map((run) => (
                <tr key={run.run_id} className="border-b border-border last:border-0 hover:bg-bg-subtle">
                  <td className="py-2.5 px-3 font-[family-name:var(--font-mono)] text-[13px]">
                    {run.run_id}
                  </td>
                  <td className="py-2.5 px-3">{run.workflow_name}</td>
                  <td className="py-2.5 px-3">
                    <Badge variant="warning">Waiting</Badge>
                  </td>
                  <td className="py-2.5 px-3 text-text-secondary text-[13px]">
                    {new Date(run.updated_at).toLocaleString()}
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleApprove(run.run_id)}
                        className="flex items-center gap-1 px-2 py-1 text-xs rounded border border-success/30 text-success hover:bg-success/10"
                      >
                        <CheckCircle className="w-3 h-3" /> Approve
                      </button>
                      <button
                        onClick={() => setRejectTarget(run.run_id)}
                        className="flex items-center gap-1 px-2 py-1 text-xs rounded border border-error/30 text-error hover:bg-error/10"
                      >
                        <XCircle className="w-3 h-3" /> Reject
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {rejectTarget && (
        <Dialog
          open={!!rejectTarget}
          title="Reject Workflow"
          onClose={() => { setRejectTarget(null); setRejectReason(''); }}
        >
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">
              Provide a reason for rejecting <code className="text-[13px]">{rejectTarget}</code>:
            </p>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Reason for rejection..."
              className="w-full px-3 py-2 text-sm rounded border border-border bg-bg-surface focus:outline-none focus:border-primary"
              rows={3}
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => { setRejectTarget(null); setRejectReason(''); }}>
                Cancel
              </Button>
              <Button variant="danger" onClick={handleReject}>
                Reject
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}
