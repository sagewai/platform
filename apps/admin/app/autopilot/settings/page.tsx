'use client';

import { useCallback, useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { AutopilotStatus } from '@/utils/types';

export default function AutopilotSettingsPage() {
  const [status, setStatus] = useState<AutopilotStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    try {
      const s = await adminApi.getAutopilotStatus();
      setStatus(s);
    } catch {
      // non-fatal
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  return (
    <div className="space-y-6" data-testid="autopilot-settings-page">
      <div>
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Settings
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Autopilot configuration and tier management.
        </p>
      </div>

      {loading ? (
        <div className="animate-pulse space-y-3">
          <div className="h-16 bg-bg-subtle rounded-lg" />
          <div className="h-12 bg-bg-subtle rounded-lg" />
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-bg-surface divide-y divide-border">
          <div className="px-5 py-4">
            <p className="text-xs uppercase tracking-wide text-text-muted font-medium mb-1">
              Status
            </p>
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full ${status?.enabled ? 'bg-success' : 'bg-text-muted'}`}
              />
              <span className="text-sm text-text-primary font-medium">
                {status?.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          </div>
          {status?.enabled && (
            <div className="px-5 py-4">
              <p className="text-xs uppercase tracking-wide text-text-muted font-medium mb-1">
                Tier
              </p>
              <span className="inline-block text-xs font-semibold uppercase tracking-wide bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                {status.tier}
              </span>
            </div>
          )}
          {status?.enabled && status.quota_limit !== null && (
            <div className="px-5 py-4">
              <p className="text-xs uppercase tracking-wide text-text-muted font-medium mb-2">
                Quota
              </p>
              <div className="flex justify-between text-xs text-text-secondary mb-1">
                <span>{status.quota_used.toLocaleString()} used</span>
                <span>{status.quota_limit.toLocaleString()} limit</span>
              </div>
              <div className="h-1.5 rounded-full bg-bg-subtle overflow-hidden">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{
                    width: `${Math.min(100, Math.round((status.quota_used / status.quota_limit) * 100))}%`,
                  }}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
