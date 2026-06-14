'use client';

// System health fetches authenticated control-plane data, so it must run in the
// BROWSER, not as a Server Component. Server-side rendering happens inside the
// admin container, where (a) `localhost:8000` is the admin itself rather than the
// backend, and (b) there is no access to the user's bearer token (it lives in
// browser storage) — so the call fails/401 and the page reports the API as down.
// Fetching client-side reuses the same auth + host the other admin pages use.
// See app/page.tsx (PR #468) for the matching dashboard fix.

import { useCallback, useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { SystemHealth } from '@/utils/types';
import { Badge, Card, EmptyState } from '@/components/ui/legacy';

export default function SystemHealthPage() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setHealth(await adminApi.getSystemHealth());
    } catch {
      setError('Failed to fetch system health. The API may be unavailable.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">System Health</h1>

      {loading && (
        <div className="text-sm text-text-muted mb-md">Loading system health…</div>
      )}

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {health && (
        <>
          {/* Overall status banner */}
          <div
            className={`rounded-lg px-5 py-4 mb-lg flex justify-between items-center flex-wrap gap-3 ${
              health.status === 'healthy'
                ? 'bg-success-light text-success'
                : health.status === 'degraded'
                  ? 'bg-warning-light text-warning-dark'
                  : 'bg-bg-subtle text-text-muted'
            }`}
          >
            <div>
              <div className="text-lg font-semibold uppercase">{health.status}</div>
              <div className="text-[13px] mt-0.5">SDK Version: {health.sdk_version}</div>
            </div>
            <div className="text-xs opacity-80">
              Checked: {new Date(health.checked_at).toLocaleString()}
            </div>
          </div>

          {/* Service cards grid */}
          {health.services.length === 0 ? (
            <EmptyState title="No Services" description="No services reported." />
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-md">
              {health.services.map((svc) => (
                <Card
                  key={svc.name}
                  className={`border-l-4 ${
                    svc.status === 'healthy'
                      ? 'border-l-success'
                      : svc.status === 'unhealthy'
                        ? 'border-l-error'
                        : 'border-l-border'
                  }`}
                >
                  <div className="flex justify-between items-center mb-2">
                    <div className="text-[15px] font-semibold">{svc.name}</div>
                    <Badge variant={svc.status === 'healthy' ? 'success' : svc.status === 'unhealthy' ? 'error' : 'default'}>
                      {svc.status}
                    </Badge>
                  </div>

                  {svc.latency_ms != null && (
                    <div className="text-[13px] text-text-muted mb-1">
                      Latency: {svc.latency_ms}ms
                    </div>
                  )}

                  {svc.detail && (
                    <div className="text-[13px] text-text-muted">{svc.detail}</div>
                  )}
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
