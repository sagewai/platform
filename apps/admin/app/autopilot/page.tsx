// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Zap, ZapOff, AlertTriangle, RefreshCw } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { AutopilotMission, AutopilotStatus, AutopilotTier } from '@/utils/types';
import { TIER_DISPLAY_LABELS } from '@/utils/types';
import { AutopilotGoalInput } from '@/components/autopilot-goal-input';
import { AutopilotMissionList } from '@/components/autopilot-mission-list';
import { EmptyAutopilotPage } from '@/components/autopilot/empty-autopilot-page';
import { OnboardingNudge } from '@/components/autopilot/onboarding-nudge';
import { SystemReadinessBanner } from '@/components/autopilot/system-readiness-banner';

const TIER_OPTIONS: { tier: AutopilotTier; label: string; description: string }[] = [
  {
    tier: 'anonymous',
    label: 'Free (rate-limited)',
    description: 'No signup required. Up to 10 missions/month.',
  },
  {
    tier: 'free',
    label: 'Free + email signup',
    description: '50 missions/month, up to 10 concurrent.',
  },
  {
    tier: 'custom',
    label: 'Need higher limits?',
    description: 'Contact licensing@sagewai.ai to raise your limits.',
  },
];

function QuotaBar({ used, limit }: { used: number; limit: number | null }) {
  if (limit == null || used == null) return null;
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
  /** Pre-fills goal input when a sample-goal pill is clicked. */
  const [sampleGoal, setSampleGoal] = useState<string | undefined>(undefined);

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

      {/* First-run readiness — shows missing-config warnings the user is
          most likely to miss (no LLM provider, no search API key). */}
      <SystemReadinessBanner />

      {/* Status card */}
      {status?.enabled ? (
        <div className="bg-bg-surface border border-border rounded-xl px-5 py-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-success shrink-0" />
            <span className="text-sm font-semibold text-text-primary">
              Autopilot enabled
            </span>
            <span className="ml-auto text-[11px] font-semibold uppercase tracking-wide bg-primary/10 text-primary px-2 py-0.5 rounded-full">
              {TIER_DISPLAY_LABELS[status.tier] ?? status.tier}
            </span>
          </div>
          <QuotaBar used={status.quota_used} limit={status.quota_limit} />
          {status.quota_limit != null && (
            <p className="text-xs text-text-muted m-0">
              Need higher limits?{' '}
              <a
                href={`mailto:licensing@sagewai.ai?subject=Autopilot%20limit%20increase&body=Hi%2C%20I%27d%20like%20to%20raise%20my%20Autopilot%20limits.%20Current%20usage%3A%20${status.quota_used}%20%2F%20${status.quota_limit}%20missions.`}
                className="text-primary hover:underline underline-offset-2"
              >
                Contact us to raise them.
              </a>
            </p>
          )}
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
              Choose your usage limits. Contact us any time to raise them.
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
                  <p className="text-xs text-text-muted m-0">{opt.description}</p>
                </button>
              ))}
            </div>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleEnable}
                disabled={enabling}
                className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-semibold rounded-lg bg-primary text-white dark:text-bg-page border-none cursor-pointer hover:bg-primary/90 transition-colors disabled:opacity-50 motion-safe:active:scale-[0.98] duration-75"
              >
                <Zap size={14} />
                {enabling ? 'Enabling…' : 'Enable Autopilot'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Onboarding nudge — only when enabled, dismissable */}
      {status?.enabled && <OnboardingNudge />}

      {/* Goal input — only when enabled */}
      {status?.enabled && (
        <div className="bg-bg-surface border border-border rounded-xl px-5 py-4 space-y-3">
          <h2 className="text-base font-semibold text-text-primary m-0 font-[family-name:var(--font-heading)]">
            New goal
          </h2>
          <AutopilotGoalInput
            onMissionApproved={() => {
              setSampleGoal(undefined);
              fetchRecentMissions();
            }}
            initialGoal={sampleGoal}
          />
        </div>
      )}

      {/* Empty state hero — when enabled but no missions yet */}
      {status?.enabled && recentMissions.length === 0 && (
        <EmptyAutopilotPage onPickGoal={(g) => setSampleGoal(g)} />
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
