'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Zap, ZapOff, AlertTriangle, RefreshCw } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { AutopilotMission, AutopilotStatus, AutopilotTier } from '@/utils/types';
import { AutopilotGoalInput } from '@/components/autopilot-goal-input';
import { AutopilotMissionList } from '@/components/autopilot-mission-list';

const TIER_OPTIONS: { tier: AutopilotTier; label: string; description: string }[] = [
  {
    tier: 'anonymous',
    label: 'Try anonymously',
    description: 'No signup needed. Rate-limited per install.',
  },
  {
    tier: 'free',
    label: 'Free account',
    description: 'Higher limits. Email signup required.',
  },
  {
    tier: 'custom',
    label: 'Custom',
    description: 'Contact licensing@sagewai.ai for custom rates.',
  },
];

function QuotaBar({ used, limit }: { used: number; limit: number | null }) {
  if (limit === null) return null;
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const colour =
    pct >= 90 ? 'bg-error' : pct >= 70 ? 'bg-warning' : 'bg-success';
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-text-secondary">
        <span>{used.toLocaleString()} used</span>
        <span>{limit.toLocaleString()} limit</span>
      </div>
      <div className="h-1.5 rounded-full bg-bg-subtle overflow-hidden">
        <div className={`h-full rounded-full transition-all ${colour}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function AutopilotPage() {
  const [status, setStatus] = useState<AutopilotStatus | null>(null);
  const [recentMissions, setRecentMissions] = useState<AutopilotMission[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [apiError, setApiError] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const [selectedTier, setSelectedTier] = useState<AutopilotTier>('anonymous');
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setLoadingStatus(true);
    setApiError(false);
    try {
      const s = await adminApi.getAutopilotStatus();
      setStatus(s);
    } catch {
      setApiError(true);
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const fetchRecentMissions = useCallback(async () => {
    try {
      const res = await adminApi.listAutopilotMissions(3);
      setRecentMissions(res.missions.slice(0, 3));
    } catch {
      // non-fatal
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    if (status?.enabled) fetchRecentMissions();
  }, [status?.enabled, fetchRecentMissions]);

  async function handleEnable() {
    setEnabling(true);
    setActionError(null);
    try {
      const s = await adminApi.enableAutopilot(selectedTier);
      setStatus(s);
      fetchRecentMissions();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to enable autopilot.');
    } finally {
      setEnabling(false);
    }
  }

  async function handleDisable() {
    if (!confirm('Disable autopilot? Scheduled missions will not be cancelled.')) return;
    setDisabling(true);
    setActionError(null);
    try {
      await adminApi.disableAutopilot();
      setStatus((prev) => prev ? { ...prev, enabled: false } : prev);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to disable autopilot.');
    } finally {
      setDisabling(false);
    }
  }

  /* ── Loading ── */
  if (loadingStatus) {
    return (
      <div>
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Goals</h1>
        <div className="animate-pulse space-y-4 mt-6">
          <div className="h-32 bg-bg-subtle rounded-xl" />
          <div className="h-48 bg-bg-subtle rounded-xl" />
        </div>
      </div>
    );
  }

  /* ── API error ── */
  if (apiError) {
    return (
      <div>
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Goals</h1>
        <div className="flex items-center gap-3 bg-error/5 border border-error/20 rounded-lg px-4 py-3 mt-6">
          <AlertTriangle className="w-4 h-4 text-error shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-medium text-error m-0">Failed to load autopilot status</p>
            <p className="text-xs text-text-muted m-0 mt-0.5">
              The API server is not responding. Check that the backend is running.
            </p>
          </div>
          <button
            type="button"
            onClick={fetchStatus}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-border bg-transparent cursor-pointer hover:bg-bg-subtle text-text-secondary transition-colors"
          >
            <RefreshCw size={12} />
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">Goals</h1>
          <p className="mt-0 text-sm text-text-secondary">
            Describe goals in plain English — Autopilot routes them to the right blueprint and runs them for you.
          </p>
        </div>
        {status?.enabled && (
          <button
            type="button"
            onClick={handleDisable}
            disabled={disabling}
            className="inline-flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-border bg-transparent cursor-pointer hover:bg-bg-subtle text-text-secondary transition-colors disabled:opacity-50"
          >
            <ZapOff size={14} />
            {disabling ? 'Disabling…' : 'Disable'}
          </button>
        )}
      </div>

      {actionError && (
        <div className="flex items-start gap-3 bg-error/5 border border-error/20 rounded-lg px-4 py-3">
          <AlertTriangle className="w-4 h-4 text-error shrink-0 mt-0.5" />
          <p className="text-sm text-error m-0">{actionError}</p>
        </div>
      )}

      {/* Status card */}
      {status?.enabled ? (
        <div className="bg-bg-surface border border-border rounded-xl px-5 py-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-success shrink-0" />
            <span className="text-sm font-semibold text-text-primary">
              Autopilot enabled
            </span>
            <span className="ml-auto text-[11px] font-semibold uppercase tracking-wide bg-primary/10 text-primary px-2 py-0.5 rounded-full">
              {status.tier}
            </span>
          </div>
          <QuotaBar used={status.quota_used} limit={status.quota_limit} />
        </div>
      ) : (
        /* Enable card */
        <div className="bg-bg-surface border border-border rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-border">
            <div className="flex items-center gap-2 mb-1">
              <Zap size={16} className="text-primary" />
              <h2 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
                Enable Autopilot
              </h2>
            </div>
            <p className="text-sm text-text-secondary m-0">
              Choose a tier to get started. You can upgrade any time.
            </p>
          </div>

          <div className="p-5 space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              {TIER_OPTIONS.map((opt) => (
                <button
                  key={opt.tier}
                  type="button"
                  onClick={() => setSelectedTier(opt.tier)}
                  className={`text-left p-4 rounded-lg border-2 transition-colors cursor-pointer bg-bg-surface ${
                    selectedTier === opt.tier
                      ? 'border-primary'
                      : 'border-border hover:border-primary/40'
                  }`}
                >
                  <p className="text-sm font-semibold text-text-primary m-0 mb-1">{opt.label}</p>
                  <p className="text-xs text-text-muted m-0">
                    {opt.tier === 'custom' ? (
                      <>
                        Contact{' '}
                        <a
                          href="mailto:licensing@sagewai.ai"
                          className="text-primary underline underline-offset-2"
                          onClick={(e) => e.stopPropagation()}
                        >
                          licensing@sagewai.ai
                        </a>{' '}
                        for custom rates.
                      </>
                    ) : (
                      opt.description
                    )}
                  </p>
                </button>
              ))}
            </div>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleEnable}
                disabled={enabling}
                className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-semibold rounded-lg bg-primary text-white border-none cursor-pointer hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                <Zap size={14} />
                {enabling ? 'Enabling…' : 'Enable Autopilot'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Goal input — only when enabled */}
      {status?.enabled && (
        <div className="bg-bg-surface border border-border rounded-xl px-5 py-4 space-y-3">
          <h2 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
            New goal
          </h2>
          <AutopilotGoalInput onMissionApproved={fetchRecentMissions} />
        </div>
      )}

      {/* Recent missions preview */}
      {status?.enabled && recentMissions.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
              Recent missions
            </h2>
            <Link
              href="/autopilot/missions"
              className="text-xs text-primary hover:underline underline-offset-2"
            >
              View all →
            </Link>
          </div>
          <AutopilotMissionList missions={recentMissions} />
        </div>
      )}
    </div>
  );
}
