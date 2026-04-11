'use client';

import { useState, useMemo } from 'react';
import Link from 'next/link';
import { Badge, Card, Skeleton, EmptyState, Button } from '@/components/ui/legacy';
import { Server, Search, CheckCircle, XCircle, Clock, ShieldOff, Shield, Zap } from 'lucide-react';
import type { FleetWorker } from '@/utils/types';

// TODO: wire to adminApi.listFleetWorkers()
const DEMO_WORKERS: FleetWorker[] = [
  {
    id: 'w-001',
    name: 'gpu-worker-us-east',
    org_id: 'org-1',
    approval_status: 'approved',
    capabilities: {
      models_supported: ['gpt-4o', 'claude-sonnet-4-6'],
      models_canonical: [],
      pool: 'gpu-cluster',
      max_concurrent: 4,
      labels: {},
      sdk_version: '0.1.0',
    },
    last_heartbeat: new Date(Date.now() - 30000).toISOString(),
    last_probe_at: null,
    probe_status: null,
    registered_at: '2026-03-28T10:00:00Z',
    approved_at: '2026-03-28T10:05:00Z',
    approved_by: 'admin@sagewai.dev',
    // TODO: wire to API — enterprise fields
    ip_allowlist: ['10.0.0.0/8', '172.16.0.0/12'],
    requires_dual_approval: true,
    connection_type: 'websocket' as const,
  },
  {
    id: 'w-002',
    name: 'cpu-worker-eu-west',
    org_id: 'org-1',
    approval_status: 'approved',
    capabilities: {
      models_supported: ['llama3-70b', 'mistral-7b'],
      models_canonical: [],
      pool: 'cpu-pool',
      max_concurrent: 2,
      labels: {},
      sdk_version: '0.1.0',
    },
    last_heartbeat: new Date(Date.now() - 120000).toISOString(),
    last_probe_at: null,
    probe_status: null,
    registered_at: '2026-03-29T14:00:00Z',
    approved_at: '2026-03-29T14:02:00Z',
    approved_by: 'admin@sagewai.dev',
    // TODO: wire to API — enterprise fields
    ip_allowlist: [],
    requires_dual_approval: false,
    connection_type: 'http' as const,
  },
  {
    id: 'w-003',
    name: 'edge-worker-apac',
    org_id: 'org-1',
    approval_status: 'pending',
    capabilities: {
      models_supported: ['gemini-2.0-flash'],
      models_canonical: [],
      pool: 'edge',
      max_concurrent: 1,
      labels: {},
      sdk_version: '0.1.0',
    },
    last_heartbeat: null,
    last_probe_at: null,
    probe_status: null,
    registered_at: '2026-03-31T08:00:00Z',
    approved_at: null,
    approved_by: null,
    // TODO: wire to API — enterprise fields
    ip_allowlist: ['192.168.1.0/24'],
    requires_dual_approval: true,
    connection_type: 'http' as const,
  },
  {
    id: 'w-004',
    name: 'dev-local-macbook',
    org_id: 'org-1',
    approval_status: 'approved',
    capabilities: {
      models_supported: ['ollama/llama3:8b'],
      models_canonical: [],
      pool: 'default',
      max_concurrent: 1,
      labels: {},
      sdk_version: '0.1.0',
    },
    last_heartbeat: new Date(Date.now() - 300000).toISOString(),
    last_probe_at: null,
    probe_status: null,
    registered_at: '2026-03-25T09:00:00Z',
    approved_at: '2026-03-25T09:01:00Z',
    approved_by: 'admin@sagewai.dev',
    // TODO: wire to API — enterprise fields
    connection_type: 'websocket' as const,
  },
  {
    id: 'w-005',
    name: 'retired-worker',
    org_id: 'org-1',
    approval_status: 'revoked',
    capabilities: {
      models_supported: ['gpt-3.5-turbo'],
      models_canonical: [],
      pool: 'default',
      max_concurrent: 1,
      labels: {},
      sdk_version: '0.0.9',
    },
    last_heartbeat: null,
    last_probe_at: null,
    probe_status: null,
    registered_at: '2026-03-20T12:00:00Z',
    approved_at: '2026-03-20T12:01:00Z',
    approved_by: 'admin@sagewai.dev',
    // TODO: wire to API — enterprise fields
    connection_type: 'http' as const,
  },
];

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
  const [workers, setWorkers] = useState<FleetWorker[]>(DEMO_WORKERS);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [poolFilter, setPoolFilter] = useState('');

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

  function handleApprove(id: string) {
    // TODO: wire to adminApi.approveFleetWorker(id)
    setWorkers((prev) =>
      prev.map((w) =>
        w.id === id
          ? { ...w, approval_status: 'approved' as const, approved_at: new Date().toISOString(), approved_by: 'admin@sagewai.dev' }
          : w,
      ),
    );
  }

  function handleReject(id: string) {
    // TODO: wire to adminApi.rejectFleetWorker(id)
    setWorkers((prev) =>
      prev.map((w) => (w.id === id ? { ...w, approval_status: 'rejected' as const } : w)),
    );
  }

  function handleRevoke(id: string) {
    // TODO: wire to adminApi.revokeFleetWorker(id)
    setWorkers((prev) =>
      prev.map((w) => (w.id === id ? { ...w, approval_status: 'revoked' as const } : w)),
    );
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
        {filtered.length === 0 ? (
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
                      <Link
                        href={`/fleet/workers/${w.id}`}
                        className="text-primary hover:underline no-underline"
                      >
                        {w.name}
                      </Link>
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
                      <div className="flex flex-wrap gap-1">
                        {w.capabilities.models_supported.map((m) => (
                          <span
                            key={m}
                            className="inline-block px-1.5 py-0.5 text-[11px] bg-white/5 border border-border rounded"
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
