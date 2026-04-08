'use client';

import { useState, useMemo } from 'react';
import { Badge, Card, EmptyState } from '@sagecurator/ui';
import { Search, ChevronDown, ChevronRight } from 'lucide-react';
import type { FleetAuditEvent } from '@/utils/types';

// TODO: wire to adminApi.listFleetAuditEvents()
const DEMO_AUDIT: FleetAuditEvent[] = [
  {
    id: 'ae-001',
    org_id: 'org-1',
    event_type: 'worker.registered',
    worker_id: 'w-003',
    details: { auto_approved: false },
    created_at: '2026-03-31T08:00:00Z',
  },
  {
    id: 'ae-002',
    org_id: 'org-1',
    event_type: 'worker.approved',
    worker_id: 'w-001',
    details: { approved_by: 'admin@sagewai.dev' },
    created_at: '2026-03-28T10:05:00Z',
  },
  {
    id: 'ae-003',
    org_id: 'org-1',
    event_type: 'enrollment_key.created',
    worker_id: null,
    details: { key_id: 'ek-001', name: 'GPU Cluster Onboarding' },
    created_at: '2026-03-28T10:00:00Z',
  },
  {
    id: 'ae-004',
    org_id: 'org-1',
    event_type: 'run.claimed',
    worker_id: 'w-001',
    details: { run_id: 'run-abc' },
    created_at: '2026-03-31T09:15:00Z',
  },
  {
    id: 'ae-005',
    org_id: 'org-1',
    event_type: 'worker.revoked',
    worker_id: 'w-005',
    details: {},
    created_at: '2026-03-30T16:00:00Z',
  },
  {
    id: 'ae-006',
    org_id: 'org-1',
    event_type: 'enrollment_key.used',
    worker_id: 'w-004',
    details: { key_id: 'ek-002' },
    created_at: '2026-03-25T09:05:00Z',
  },
  {
    id: 'ae-007',
    org_id: 'org-1',
    event_type: 'run.reported',
    worker_id: 'w-002',
    details: { run_id: 'run-xyz', status: 'completed' },
    created_at: '2026-03-31T09:20:00Z',
  },
  {
    id: 'ae-008',
    org_id: 'org-1',
    event_type: 'token.issued',
    worker_id: 'w-001',
    details: {},
    created_at: '2026-03-28T10:05:00Z',
  },
];

const EVENT_TYPE_VARIANTS: Record<string, 'success' | 'error' | 'warning' | 'info' | 'default'> = {
  'worker.registered': 'info',
  'worker.approved': 'success',
  'worker.rejected': 'error',
  'worker.revoked': 'error',
  'enrollment_key.created': 'warning',
  'enrollment_key.used': 'warning',
  'enrollment_key.revoked': 'warning',
  'enrollment_key.expired': 'warning',
  'run.claimed': 'default',
  'run.reported': 'default',
  'token.issued': 'info',
  'token.refreshed': 'info',
  'token.revoked': 'info',
};

const ALL_EVENT_TYPES = [
  'worker.registered',
  'worker.approved',
  'worker.rejected',
  'worker.revoked',
  'enrollment_key.created',
  'enrollment_key.used',
  'enrollment_key.revoked',
  'enrollment_key.expired',
  'run.claimed',
  'run.reported',
  'token.issued',
  'token.refreshed',
  'token.revoked',
];

export default function FleetAuditPage() {
  const [events] = useState<FleetAuditEvent[]>(DEMO_AUDIT);
  const [typeFilter, setTypeFilter] = useState('');
  const [workerFilter, setWorkerFilter] = useState('');
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const sorted = useMemo(() => {
    return [...events].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  }, [events]);

  const filtered = useMemo(() => {
    return sorted.filter((e) => {
      if (typeFilter && e.event_type !== typeFilter) return false;
      if (workerFilter && !(e.worker_id ?? '').toLowerCase().includes(workerFilter.toLowerCase()))
        return false;
      return true;
    });
  }, [sorted, typeFilter, workerFilter]);

  function toggleExpand(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-lg">
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Fleet Audit Log
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Track all fleet operations including worker registrations, approvals, and task assignments.
        </p>
      </div>

      {/* Filters */}
      <Card className="mb-md">
        <div className="flex gap-3 items-center flex-wrap">
          <label className="text-[13px] text-text-muted">
            Event type:
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="ml-1.5 px-2.5 py-1.5 border border-border rounded text-[13px] bg-bg-surface"
            >
              <option value="">All types</option>
              {ALL_EVENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="text-[13px] text-text-muted flex items-center gap-1.5">
            <Search size={14} />
            <input
              type="text"
              value={workerFilter}
              onChange={(e) => setWorkerFilter(e.target.value)}
              placeholder="Filter by worker ID..."
              className="px-2.5 py-1.5 border border-border rounded text-[13px] w-[180px] bg-bg-surface"
            />
          </label>
          <span className="text-[13px] text-text-muted ml-auto">
            {filtered.length} event{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>
      </Card>

      {/* Event list */}
      <Card>
        {filtered.length === 0 ? (
          <EmptyState
            title="No Audit Events"
            description="No fleet audit events match your filters. Events are recorded when workers register, get approved, or claim tasks."
          />
        ) : (
          <div className="divide-y divide-border">
            {filtered.map((evt) => {
              const isExpanded = expandedIds.has(evt.id);
              const hasDetails = Object.keys(evt.details).length > 0;
              return (
                <div key={evt.id} className="py-3 px-3 hover:bg-bg-subtle transition-colors">
                  <div className="flex items-center gap-3">
                    {/* Expand toggle */}
                    <button
                      onClick={() => hasDetails && toggleExpand(evt.id)}
                      className={`shrink-0 bg-transparent border-0 cursor-pointer p-0 ${
                        hasDetails ? 'text-text-muted hover:text-text-primary' : 'text-transparent'
                      }`}
                      disabled={!hasDetails}
                      aria-label={isExpanded ? 'Collapse details' : 'Expand details'}
                    >
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    </button>

                    {/* Timestamp */}
                    <span className="text-xs text-text-muted w-[140px] shrink-0">
                      {new Date(evt.created_at).toLocaleString()}
                    </span>

                    {/* Event type badge */}
                    <Badge variant={EVENT_TYPE_VARIANTS[evt.event_type] ?? 'default'}>
                      {evt.event_type}
                    </Badge>

                    {/* Worker ID */}
                    <span className="text-[13px] text-text-secondary font-mono">
                      {evt.worker_id ?? '--'}
                    </span>
                  </div>

                  {/* Expanded details */}
                  {isExpanded && hasDetails && (
                    <div className="ml-[26px] mt-2 pl-3 border-l-2 border-border">
                      <pre className="text-xs text-text-secondary m-0 overflow-x-auto whitespace-pre-wrap font-mono bg-black/20 rounded p-2">
                        {JSON.stringify(evt.details, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
