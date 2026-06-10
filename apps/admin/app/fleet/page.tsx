'use client';

import { useState, useMemo, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { Badge, Card, Skeleton, EmptyState, Button, useToast } from '@/components/ui/legacy';
import { Server, Search, CheckCircle, XCircle, Clock, ShieldOff, Shield, Zap } from 'lucide-react';
import type { FleetWorker } from '@/utils/types';
import { adminApi } from '@/utils/api';
import { SandboxSummary, UnsandboxedBadge } from './_components/SandboxSummary';

const STATUS_VARIANTS: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
  approved: 'success',
  pending: 'warning',
  rejected: 'error',
  revoked: 'default',
};

function relativeTime(iso: string | null): string {
  if (!iso) return '--';
  const diff = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function FleetWorkersPage() {
  const [workers, setWorkers] = useState<FleetWorker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [poolFilter, setPoolFilter] = useState('');
  const { toast } = useToast();

  const fetchWorkers = useCallback(async () => {
    try {
      const data = await adminApi.listFleetWorkers();
      setWorkers(data.workers);
      setError(null);
    } catch {
      setError('Failed to load fleet workers.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchWorkers(); }, [fetchWorkers]);

  const pools = useMemo(
    () => [...new Set(workers.map((w) => w.capabilities.pool))].sort(),
    [workers],
  );

  const filtered = useMemo(() => {
    return workers.filter((w) => {
      if (searchQuery && !w.name.toLowerCase().includes(searchQuery.toLowerCase())) return false;
      if (statusFilter && w.approval_status !== statusFilter) return false;
      if (poolFilter && w.capabilities.pool !== poolFilter) return false;
      return true;
    });
  }, [workers, searchQuery, statusFilter, poolFilter]);

  const stats = useMemo(() => {
    const total = workers.length;
    const approved = workers.filter((w) => w.approval_status === 'approved').length;
    const pending = workers.filter((w) => w.approval_status === 'pending').length;
    const revoked = workers.filter((w) => w.approval_status === 'revoked').length;
    return { total, approved, pending, revoked };
  }, [workers]);

  async function handleApprove(id: string) {
    try {
      let approvedBy = 'admin';
      try {
        const me = await adminApi.getMe();
        approvedBy = me.email || me.display_name || 'admin';
      } catch {
        /* fall back to generic label */
      }
      const { worker } = await adminApi.approveFleetWorker(id, approvedBy);
      setWorkers((prev) => prev.map((w) => (w.id === id ? worker : w)));
      toast('success', 'Worker approved');
    } catch {
      toast('error', 'Failed to approve worker');
    }
  }

  async function handleReject(id: string) {
    try {
      const { worker } = await adminApi.rejectFleetWorker(id);
      setWorkers((prev) => prev.map((w) => (w.id === id ? worker : w)));
      toast('success', 'Worker rejected');
    } catch {
      toast('error', 'Failed to reject worker');
    }
  }

  async function handleRevoke(id: string) {
    try {
      const { worker } = await adminApi.revokeFleetWorker(id);
      setWorkers((prev) => prev.map((w) => (w.id === id ? worker : w)));
      toast('success', 'Worker revoked');
    } catch {
      toast('error', 'Failed to revoke worker');
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
            Fleet Workers
          </h1>
          <p className="mt-0 text-sm text-text-secondary">
            Manage registered workers across your infrastructure.
          </p>
        </div>
      </div>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-4 py-3 text-error text-sm mb-md" role="alert">
          {error}
        </div>
      )}

      {/* Stats bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-md">
        <Card className="!p-4">
          <div className="flex items-center gap-2">
            <Server size={16} className="text-text-muted" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Total</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats.total}</div>
        </Card>
        <Card className="!p-4">
          <div className="flex items-center gap-2">
            <CheckCircle size={16} className="text-success" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Approved</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats.approved}</div>
        </Card>
        <Card className="!p-4">
          <div className="flex items-center gap-2">
            <Clock size={16} className="text-amber-500" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Pending</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats.pending}</div>
        </Card>
        <Card className="!p-4">
          <div className="flex items-center gap-2">
            <ShieldOff size={16} className="text-text-muted" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Revoked</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats.revoked}</div>
        </Card>
      </div>

      {/* Filter bar */}
      <Card className="mb-md">
        <div className="flex gap-3 items-center flex-wrap">
          <label className="text-[13px] text-text-muted flex items-center gap-1.5">
            <Search size={14} />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search workers..."
              className="px-2.5 py-1.5 border border-border rounded text-[13px] w-[180px] bg-bg-surface"
            />
          </label>
          <label className="text-[13px] text-text-muted">
            Status:
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="ml-1.5 px-2.5 py-1.5 border border-border rounded text-[13px] bg-bg-surface"
            >
              <option value="">All</option>
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="revoked">Revoked</option>
            </select>
          </label>
          <label className="text-[13px] text-text-muted">
            Pool:
            <select
              value={poolFilter}
              onChange={(e) => setPoolFilter(e.target.value)}
              className="ml-1.5 px-2.5 py-1.5 border border-border rounded text-[13px] bg-bg-surface"
            >
              <option value="">All pools</option>
              {pools.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <span className="text-[13px] text-text-muted ml-auto">
            {filtered.length} worker{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>
      </Card>

      {/* Workers table */}
      <Card>
        {loading ? (
          <Skeleton lines={5} />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No Workers Found"
            description="No workers match your current filters. Workers will appear here once they register with the fleet."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-border">
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Name
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Status
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Pool
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Sandbox
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Models
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Last Heartbeat
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((w) => (
                  <tr
                    key={w.id}
                    className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors"
                  >
                    <td className="py-2.5 px-3 font-medium">
                      <div className="flex items-center gap-0">
                        <Link
                          href={`/fleet/workers/${w.id}`}
                          className="text-primary hover:underline no-underline"
                        >
                          {w.name}
                        </Link>
                        <UnsandboxedBadge labels={w.capabilities.labels} />
                      </div>
                      <div className="text-[11px] text-text-muted">{w.id}</div>
                    </td>
                    <td className="py-2.5 px-3">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant={STATUS_VARIANTS[w.approval_status] ?? 'default'}>
                          {w.approval_status}
                        </Badge>
                        {w.ip_allowlist && w.ip_allowlist.length > 0 && (
                          <span className="text-[10px] text-text-muted flex items-center gap-1">
                            <Shield size={10} /> IP restricted
                          </span>
                        )}
                        {w.requires_dual_approval && (
                          <Badge variant="info" className="text-[10px]">Dual Approval</Badge>
                        )}
                        {w.connection_type === 'websocket' && (
                          <span className="text-[10px] text-primary flex items-center gap-1">
                            <Zap size={10} /> WebSocket
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-2.5 px-3 text-[13px]">{w.capabilities.pool}</td>
                    <td className="py-2.5 px-3">
                      <SandboxSummary labels={w.capabilities.labels} />
                    </td>
                    <td className="py-2.5 px-3">
                      <div className="flex flex-wrap gap-1">
                        {w.capabilities.models_supported.map((m) => (
                          <span
                            key={m}
                            className="inline-block px-1.5 py-0.5 text-[11px] bg-bg-subtle border border-border rounded"
                          >
                            {m}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-2.5 px-3 text-[13px] text-text-muted">
                      {relativeTime(w.last_heartbeat)}
                    </td>
                    <td className="py-2.5 px-3">
                      <div className="flex gap-1.5">
                        {w.approval_status === 'pending' && (
                          <>
                            <Button
                              variant="primary"
                              size="sm"
                              onClick={() => handleApprove(w.id)}
                            >
                              Approve
                            </Button>
                            <Button
                              variant="danger"
                              size="sm"
                              onClick={() => handleReject(w.id)}
                            >
                              Reject
                            </Button>
                          </>
                        )}
                        {w.approval_status === 'approved' && (
                          <Button variant="danger" size="sm" onClick={() => handleRevoke(w.id)}>
                            Revoke
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
