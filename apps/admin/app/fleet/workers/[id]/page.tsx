'use client';

import { useState, useMemo } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { Badge, Card, Button, EmptyState } from '@sagecurator/ui';
import { ArrowLeft, Heart, Cpu, Shield, AlertTriangle, Zap } from 'lucide-react';
import type { FleetWorker } from '@/utils/types';

// TODO: wire to adminApi.getFleetWorker(id)
const DEMO_WORKERS: FleetWorker[] = [
  {
    id: 'w-001',
    name: 'gpu-worker-us-east',
    org_id: 'org-1',
    approval_status: 'approved',
    capabilities: {
      models_supported: ['gpt-4o', 'claude-sonnet-4-6'],
      models_canonical: ['openai/gpt-4o', 'anthropic/claude-sonnet-4-6'],
      pool: 'gpu-cluster',
      max_concurrent: 4,
      labels: { region: 'us-east-1', gpu: 'a100' },
      sdk_version: '0.1.0',
    },
    last_heartbeat: new Date(Date.now() - 30000).toISOString(),
    last_probe_at: new Date(Date.now() - 60000).toISOString(),
    probe_status: 'healthy',
    registered_at: '2026-03-28T10:00:00Z',
    approved_at: '2026-03-28T10:05:00Z',
    approved_by: 'admin@sagewai.dev',
    // TODO: wire to API — enterprise fields
    ip_allowlist: ['10.0.0.0/8', '172.16.0.0/12'],
    requires_dual_approval: true,
    connection_type: 'websocket' as const,
    probe_results: [
      { model: 'gpt-4o', reachable: true, latency_ms: 45, error: null },
      { model: 'claude-sonnet-4-6', reachable: true, latency_ms: 62, error: null },
    ],
    anomalies: [],
  },
  {
    id: 'w-002',
    name: 'cpu-worker-eu-west',
    org_id: 'org-1',
    approval_status: 'approved',
    capabilities: {
      models_supported: ['llama3-70b', 'mistral-7b'],
      models_canonical: ['meta/llama3-70b', 'mistral/mistral-7b'],
      pool: 'cpu-pool',
      max_concurrent: 2,
      labels: { region: 'eu-west-1' },
      sdk_version: '0.1.0',
    },
    last_heartbeat: new Date(Date.now() - 120000).toISOString(),
    last_probe_at: null,
    probe_status: null,
    registered_at: '2026-03-29T14:00:00Z',
    approved_at: '2026-03-29T14:02:00Z',
    approved_by: 'admin@sagewai.dev',
    // TODO: wire to API — enterprise fields
    connection_type: 'http' as const,
    probe_results: [
      { model: 'llama3-70b', reachable: true, latency_ms: 120, error: null },
      { model: 'mistral-7b', reachable: false, latency_ms: null, error: 'connection timeout' },
    ],
    anomalies: [
      { type: 'excessive_failures', message: '8 failed reports in the last hour (threshold: 10)', detected_at: '2026-03-31T09:30:00Z' },
      { type: 'heartbeat_timeout', message: 'No heartbeat for 12 minutes (threshold: 5m)', detected_at: '2026-03-31T09:25:00Z' },
    ],
  },
  {
    id: 'w-003',
    name: 'edge-worker-apac',
    org_id: 'org-1',
    approval_status: 'pending',
    capabilities: {
      models_supported: ['gemini-2.0-flash'],
      models_canonical: ['google/gemini-2.0-flash'],
      pool: 'edge',
      max_concurrent: 1,
      labels: { region: 'ap-southeast-1' },
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
      models_canonical: ['ollama/llama3:8b'],
      pool: 'default',
      max_concurrent: 1,
      labels: { env: 'dev' },
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
    probe_results: [
      { model: 'ollama/llama3:8b', reachable: true, latency_ms: 15, error: null },
    ],
    anomalies: [],
  },
  {
    id: 'w-005',
    name: 'retired-worker',
    org_id: 'org-1',
    approval_status: 'revoked',
    capabilities: {
      models_supported: ['gpt-3.5-turbo'],
      models_canonical: ['openai/gpt-3.5-turbo'],
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

function formatDate(iso: string | null): string {
  if (!iso) return '--';
  return new Date(iso).toLocaleString();
}

type TabId = 'overview' | 'capabilities' | 'activity';

export default function WorkerDetailPage() {
  const params = useParams();
  const workerId = params.id as string;
  const [activeTab, setActiveTab] = useState<TabId>('overview');

  // TODO: wire to adminApi.getFleetWorker(workerId)
  const worker = useMemo(
    () => DEMO_WORKERS.find((w) => w.id === workerId) ?? null,
    [workerId],
  );

  const [localWorker, setLocalWorker] = useState<FleetWorker | null>(worker);

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

  function handleApprove() {
    // TODO: wire to adminApi.approveFleetWorker(workerId)
    setLocalWorker((prev) =>
      prev
        ? { ...prev, approval_status: 'approved' as const, approved_at: new Date().toISOString(), approved_by: 'admin@sagewai.dev' }
        : prev,
    );
  }

  function handleReject() {
    // TODO: wire to adminApi.rejectFleetWorker(workerId)
    setLocalWorker((prev) =>
      prev ? { ...prev, approval_status: 'rejected' as const } : prev,
    );
  }

  function handleRevoke() {
    // TODO: wire to adminApi.revokeFleetWorker(workerId)
    setLocalWorker((prev) =>
      prev ? { ...prev, approval_status: 'revoked' as const } : prev,
    );
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
                ? 'border-primary text-white'
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
                  className="inline-block px-2.5 py-1 text-[13px] bg-white/5 border border-border rounded-md"
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
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-[13px] bg-white/5 border border-border rounded-md"
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
          <p className="text-sm text-text-secondary m-0">
            Audit events for this worker will appear here once the fleet API is connected.
          </p>
        </Card>
      )}
    </div>
  );
}
