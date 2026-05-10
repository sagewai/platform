// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { useCallback, useEffect, useReducer, useState } from 'react';
import { RefreshCw, Search } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { AutopilotMission, AutopilotMissionStatus } from '@/utils/types';
import { AutopilotMissionList } from '@/components/autopilot-mission-list';
import { useMissionEvents, type MissionStatusEvent } from '@/lib/autopilot/use-mission-events';
import { EmptyMissionsPage } from '@/components/autopilot/empty-missions-page';

type LiveStatuses = Record<string, string>;

function liveStatusReducer(
  state: LiveStatuses,
  event: MissionStatusEvent,
): LiveStatuses {
  return { ...state, [event.mission_id]: event.new_status };
}

const STATUS_FILTERS: { value: AutopilotMissionStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
];

const PAGE_SIZE = 25;

export default function MissionsPage() {
  const [missions, setMissions] = useState<AutopilotMission[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [statusFilter, setStatusFilter] = useState<AutopilotMissionStatus | 'all'>('all');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [cancelModal, setCancelModal] = useState<{ id: string } | null>(null);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelling, setCancelling] = useState(false);
  const [liveStatuses, dispatch] = useReducer(liveStatusReducer, {});

  // Debounce the search input so we don't fire a request per keystroke.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(id);
  }, [search]);

  const fetchMissions = useCallback(
    async (opts?: { offset?: number; append?: boolean }) => {
      const offset = opts?.offset ?? 0;
      const append = opts?.append ?? false;
      if (append) setLoadingMore(true);
      else setLoading(true);
      try {
        const res = await adminApi.listAutopilotMissions({
          limit: PAGE_SIZE,
          offset,
          q: debouncedSearch || undefined,
          status: statusFilter === 'all' ? undefined : statusFilter,
        });
        setTotal(res.total ?? res.missions.length);
        setHasMore(Boolean(res.has_more));
        setMissions((prev) => (append ? [...prev, ...res.missions] : res.missions));
      } catch {
        // non-fatal
      } finally {
        if (append) setLoadingMore(false);
        else setLoading(false);
      }
    },
    [debouncedSearch, statusFilter],
  );

  // Refetch from the top whenever the search query or status filter changes.
  useEffect(() => {
    fetchMissions({ offset: 0, append: false });
  }, [fetchMissions]);

  const handleEvent = useCallback((e: MissionStatusEvent) => {
    dispatch(e);
  }, []);

  useMissionEvents(handleEvent);

  async function handleCancel() {
    if (!cancelModal) return;
    setCancelling(true);
    try {
      await adminApi.cancelAutopilotMission(cancelModal.id, cancelReason);
      setCancelModal(null);
      setCancelReason('');
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to cancel mission');
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Missions
        </h1>
        <button
          type="button"
          onClick={() => fetchMissions({ offset: 0, append: false })}
          disabled={loading}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border border-border bg-transparent cursor-pointer hover:bg-bg-subtle text-text-secondary transition-colors disabled:opacity-50"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search
          size={14}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none"
        />
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by goal, blueprint, category, or mission id…"
          className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-border bg-bg-surface text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent transition-colors"
        />
      </div>

      {/* Status filter chips */}
      <div className="flex flex-wrap gap-2">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setStatusFilter(f.value)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors cursor-pointer ${
              statusFilter === f.value
                ? 'border-accent bg-accent/10 text-accent-foreground font-medium'
                : 'border-border text-text-secondary hover:border-accent/40'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="animate-pulse space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 bg-bg-subtle rounded-lg" />
          ))}
        </div>
      ) : missions.length === 0 ? (
        debouncedSearch || statusFilter !== 'all' ? (
          <p className="text-sm text-text-muted py-8 text-center m-0">
            No missions match these filters.
          </p>
        ) : (
          <EmptyMissionsPage />
        )
      ) : (
        <>
          <AutopilotMissionList
            missions={missions}
            liveStatuses={liveStatuses}
            onCancel={(id) => {
              setCancelModal({ id });
              setCancelReason('');
            }}
          />
          <div className="flex items-center justify-between text-xs text-text-muted">
            <span>
              Showing {missions.length} of {total}
            </span>
            {hasMore && (
              <button
                type="button"
                onClick={() => fetchMissions({ offset: missions.length, append: true })}
                disabled={loadingMore}
                className="px-3 py-1.5 rounded-md border border-border bg-bg-surface cursor-pointer hover:bg-bg-subtle text-text-secondary transition-colors disabled:opacity-50"
              >
                {loadingMore ? 'Loading…' : 'Load more'}
              </button>
            )}
          </div>
        </>
      )}

      {/* Cancel confirmation modal */}
      {cancelModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-bg-surface border border-border rounded-xl p-6 max-w-md w-full space-y-4">
            <h2 className="text-base font-semibold text-text-primary m-0">Cancel mission</h2>
            <p className="text-sm text-text-secondary m-0">
              Provide a reason for cancellation. The mission will stop after its current step.
            </p>
            <textarea
              className="w-full text-sm border border-border rounded-lg px-3 py-2 bg-bg-subtle text-text-primary resize-none h-24 focus:outline-none focus:border-accent"
              placeholder="Why are you cancelling this mission?"
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setCancelModal(null)}
                className="px-4 py-2 text-sm rounded-lg border border-border bg-transparent cursor-pointer hover:bg-bg-subtle text-text-secondary transition-colors"
              >
                Never mind
              </button>
              <button
                type="button"
                onClick={handleCancel}
                disabled={!cancelReason.trim() || cancelling}
                className="px-4 py-2 text-sm rounded-lg bg-error text-text-on-dark border-none cursor-pointer hover:bg-error/90 transition-colors disabled:opacity-50 motion-safe:active:scale-[0.98] duration-75"
              >
                {cancelling ? 'Cancelling…' : 'Cancel mission'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
