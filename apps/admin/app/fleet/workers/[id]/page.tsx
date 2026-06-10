'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { Badge, Card, Button, EmptyState, Skeleton, useToast } from '@/components/ui/legacy';
import { ArrowLeft, Heart, Cpu, Shield, AlertTriangle, Zap } from 'lucide-react';
import type { FleetWorker, FleetAuditEvent } from '@/utils/types';
import { adminApi } from '@/utils/api';
import { PoolStatsPanel } from '@/components/pool-stats-panel';

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

function formatDate(iso: string | null): string {
  if (!iso) return '--';
  return new Date(iso).toLocaleString();
}

type TabId = 'overview' | 'capabilities' | 'activity';

export default function WorkerDetailPage() {
  const params = useParams();
  const workerId = params.id as string;
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const { toast } = useToast();

  const [localWorker, setLocalWorker] = useState<FleetWorker | null>(null);
  const [loading, setLoading] = useState(true);
  const [auditEvents, setAuditEvents] = useState<FleetAuditEvent[]>([]);
  const [auditLoaded, setAuditLoaded] = useState(false);

  const fetchWorker = useCallback(async () => {
    try {
      const { worker } = await adminApi.getFleetWorker(workerId);
      setLocalWorker(worker);
    } catch {
      setLocalWorker(null);
    } finally {
      setLoading(false);
    }
  }, [workerId]);

  useEffect(() => { fetchWorker(); }, [fetchWorker]);

  useEffect(() => {
    if (activeTab !== 'activity' || auditLoaded) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await adminApi.listFleetAudit({ worker_id: workerId });
        if (!cancelled) setAuditEvents(data.events);
      } catch {
        if (!cancelled) setAuditEvents([]);
      } finally {
        if (!cancelled) setAuditLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, [activeTab, auditLoaded, workerId]);

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto">
        <Link href="/fleet" className="text-primary text-sm hover:underline no-underline flex items-center gap-1 mb-md">
          <ArrowLeft size={14} /> Back to Workers
        </Link>
        <Card className="!p-5"><Skeleton lines={6} /></Card>
      </div>
    );
  }

  if (!localWorker) {
    return (
      <div className="max-w-4xl mx-auto">
        <Link href="/fleet" className="text-primary text-sm hover:underline no-underline flex items-center gap-1 mb-md">
          <ArrowLeft size={14} /> Back to Workers
        </Link>
        <EmptyState
          title="Worker Not Found"
          description={`No worker found with ID "${workerId}". It may have been removed.`}
        />
      </div>
    );
  }

  async function handleApprove() {
    try {
      let approvedBy = 'admin';
      try {
        const me = await adminApi.getMe();
        approvedBy = me.email || me.display_name || 'admin';
      } catch {
        /* fall back to generic label */
      }
      const { worker } = await adminApi.approveFleetWorker(workerId, approvedBy);
      setLocalWorker(worker);
      toast('success', 'Worker approved');
    } catch {
      toast('error', 'Failed to approve worker');
    }
  }

  async function handleReject() {
    try {
      const { worker } = await adminApi.rejectFleetWorker(workerId);
      setLocalWorker(worker);
      toast('success', 'Worker rejected');
    } catch {
      toast('error', 'Failed to reject worker');
    }
  }

  async function handleRevoke() {
    try {
      const { worker } = await adminApi.revokeFleetWorker(workerId);
      setLocalWorker(worker);
      toast('success', 'Worker revoked');
    } catch {
      toast('error', 'Failed to revoke worker');
    }
  }

  const tabs: { id: TabId; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'capabilities', label: 'Capabilities' },
    { id: 'activity', label: 'Activity' },
  ];

  return (
    <div className="max-w-4xl mx-auto">
      <Link href="/fleet" className="text-primary text-sm hover:underline no-underline flex items-center gap-1 mb-md">
        <ArrowLeft size={14} /> Back to Workers
      </Link>

      {/* Header */}
      <div className="flex justify-between items-start mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
            {localWorker.name}
          </h1>
          <div className="flex items-center gap-2 text-sm text-text-secondary flex-wrap">
            <span className="font-mono text-xs">{localWorker.id}</span>
            <Badge variant={STATUS_VARIANTS[localWorker.approval_status] ?? 'default'}>
              {localWorker.approval_status}
            </Badge>
            {localWorker.ip_allowlist && localWorker.ip_allowlist.length > 0 && (
              <span className="text-[10px] text-text-muted flex items-center gap-1">
                <Shield size={10} /> IP restricted
              </span>
            )}
            {localWorker.requires_dual_approval && (
              <Badge variant="info" className="text-[10px]">Dual Approval</Badge>
            )}
            {localWorker.connection_type === 'websocket' && (
              <span className="text-[10px] text-primary flex items-center gap-1">
                <Zap size={10} /> WebSocket
              </span>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {localWorker.approval_status === 'pending' && (
            <>
              <Button variant="primary" onClick={handleApprove}>
                Approve
              </Button>
              <Button variant="danger" onClick={handleReject}>
                Reject
              </Button>
            </>
          )}
          {localWorker.approval_status === 'approved' && (
            <Button variant="danger" onClick={handleRevoke}>
              Revoke
            </Button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border mb-md">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-[1px] cursor-pointer bg-transparent ${
              activeTab === tab.id
                ? 'border-primary text-text-primary'
                : 'border-transparent text-text-muted hover:text-text-secondary'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Overview tab */}
      {activeTab === 'overview' && (
        <div className="grid md:grid-cols-2 gap-4">
          <Card className="!p-5">
            <div className="flex items-center gap-2 mb-4">
              <Shield size={16} className="text-text-muted" />
              <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0">
                Metadata
              </h3>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-text-muted">ID</dt>
              <dd className="font-mono text-xs m-0">{localWorker.id}</dd>
              <dt className="text-text-muted">Name</dt>
              <dd className="m-0">{localWorker.name}</dd>
              <dt className="text-text-muted">Organization</dt>
              <dd className="m-0">{localWorker.org_id}</dd>
              <dt className="text-text-muted">Status</dt>
              <dd className="m-0">
                <Badge variant={STATUS_VARIANTS[localWorker.approval_status] ?? 'default'}>
                  {localWorker.approval_status}
                </Badge>
              </dd>
              <dt className="text-text-muted">Pool</dt>
              <dd className="m-0">{localWorker.capabilities.pool}</dd>
              <dt className="text-text-muted">Registered</dt>
              <dd className="m-0">{formatDate(localWorker.registered_at)}</dd>
              <dt className="text-text-muted">Approved At</dt>
              <dd className="m-0">{formatDate(localWorker.approved_at)}</dd>
              <dt className="text-text-muted">Approved By</dt>
              <dd className="m-0">{localWorker.approved_by ?? '--'}</dd>
            </dl>
          </Card>

          <Card className="!p-5">
            <div className="flex items-center gap-2 mb-4">
              <Heart size={16} className="text-text-muted" />
              <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0">
                Health
              </h3>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-text-muted">Last Heartbeat</dt>
              <dd className="m-0">
                {localWorker.last_heartbeat
                  ? `${relativeTime(localWorker.last_heartbeat)} (${formatDate(localWorker.last_heartbeat)})`
                  : '--'}
              </dd>
              <dt className="text-text-muted">Probe Status</dt>
              <dd className="m-0">
                {localWorker.probe_status ? (
                  <Badge variant={localWorker.probe_status === 'healthy' ? 'success' : 'error'}>
                    {localWorker.probe_status}
                  </Badge>
                ) : (
                  '--'
                )}
              </dd>
              <dt className="text-text-muted">Last Probe</dt>
              <dd className="m-0">{formatDate(localWorker.last_probe_at)}</dd>
              <dt className="text-text-muted">Models</dt>
              <dd className="m-0">{localWorker.capabilities.models_supported.length} model(s)</dd>
              <dt className="text-text-muted">Max Concurrent</dt>
              <dd className="m-0">{localWorker.capabilities.max_concurrent}</dd>
              {localWorker.connection_type && (
                <>
                  <dt className="text-text-muted">Connection</dt>
                  <dd className="m-0">
                    {localWorker.connection_type === 'websocket' ? (
                      <span className="text-primary flex items-center gap-1 text-xs">
                        <Zap size={12} /> WebSocket
                      </span>
                    ) : (
                      <span className="text-xs">HTTP long-poll</span>
                    )}
                  </dd>
                </>
              )}
            </dl>
            {/* Model probe results */}
            {localWorker.probe_results && localWorker.probe_results.length > 0 && (
              <div className="mt-3 border-t border-border pt-3">
                <div className="text-[10px] uppercase tracking-widest text-text-muted mb-2">Model Probes</div>
                <div className="flex flex-col gap-1.5">
                  {localWorker.probe_results.map((probe) => (
                    <div key={probe.model} className="flex items-center justify-between text-xs">
                      <span className="font-[family-name:var(--font-mono)]">{probe.model}</span>
                      <span className={probe.reachable ? 'text-success' : 'text-error'}>
                        {probe.reachable ? `OK ${probe.latency_ms}ms` : `ERR ${probe.error || 'unreachable'}`}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>

          {/* Anomaly alerts */}
          {localWorker.anomalies && localWorker.anomalies.length > 0 && (
            <Card className="!p-5 md:col-span-2">
              <div className="flex items-center gap-2 mb-2">
                <AlertTriangle size={14} className="text-amber-500" />
                <h3 className="text-sm font-semibold m-0 text-amber-500">Anomaly Alerts</h3>
              </div>
              <div className="flex flex-col gap-2">
                {localWorker.anomalies.map((a, i) => (
                  <div key={i} className="text-xs bg-amber-500/10 rounded-md px-3 py-2 text-text-secondary">
                    <span className="font-medium text-amber-500">{a.type}</span>: {a.message}
                    <span className="text-text-muted ml-2">{new Date(a.detected_at).toLocaleString()}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Sandbox pool stats */}
          <div className="md:col-span-2">
            <PoolStatsPanel workerId={workerId} />
          </div>
        </div>
      )}

      {/* Capabilities tab */}
      {activeTab === 'capabilities' && (
        <div className="space-y-4">
          <Card className="!p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0 mb-3">
              Supported Models
            </h3>
            <div className="flex flex-wrap gap-2">
              {localWorker.capabilities.models_supported.map((m) => (
                <span
                  key={m}
                  className="inline-block px-2.5 py-1 text-[13px] bg-bg-subtle border border-border rounded-md"
                >
                  {m}
                </span>
              ))}
            </div>
          </Card>

          <Card className="!p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0 mb-3">
              Labels
            </h3>
            {Object.keys(localWorker.capabilities.labels).length === 0 ? (
              <p className="text-sm text-text-muted m-0">No labels set.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {Object.entries(localWorker.capabilities.labels).map(([k, v]) => (
                  <span
                    key={k}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-[13px] bg-bg-subtle border border-border rounded-md"
                  >
                    <span className="text-text-muted">{k}:</span>
                    <span>{v}</span>
                  </span>
                ))}
              </div>
            )}
          </Card>

          <Card className="!p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0 mb-3">
              Configuration
            </h3>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-text-muted">Max Concurrent</dt>
              <dd className="m-0">{localWorker.capabilities.max_concurrent}</dd>
              <dt className="text-text-muted">SDK Version</dt>
              <dd className="font-mono text-xs m-0">{localWorker.capabilities.sdk_version}</dd>
              <dt className="text-text-muted">Pool</dt>
              <dd className="m-0">{localWorker.capabilities.pool}</dd>
            </dl>
          </Card>
        </div>
      )}

      {/* Activity tab */}
      {activeTab === 'activity' && (
        <Card className="!p-5">
          <div className="flex items-center gap-2 mb-3">
            <Cpu size={16} className="text-text-muted" />
            <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted m-0">
              Activity
            </h3>
          </div>
          {!auditLoaded ? (
            <Skeleton lines={3} />
          ) : auditEvents.length === 0 ? (
            <p className="text-sm text-text-secondary m-0">
              No audit events recorded for this worker yet.
            </p>
          ) : (
            <div className="divide-y divide-border">
              {auditEvents.map((evt) => (
                <div key={evt.id} className="py-2.5 flex items-center gap-3">
                  <span className="text-xs text-text-muted w-[150px] shrink-0">
                    {new Date(evt.created_at).toLocaleString()}
                  </span>
                  <Badge variant="default">{evt.event_type}</Badge>
                  {Object.keys(evt.details).length > 0 && (
                    <code className="text-[11px] text-text-muted font-mono truncate">
                      {JSON.stringify(evt.details)}
                    </code>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
