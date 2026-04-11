import { adminApi } from '@/utils/api';
import type { SystemHealth } from '@/utils/types';
import { Badge, Card, EmptyState } from '@/components/ui/legacy';

export const dynamic = 'force-dynamic';

export default async function SystemHealthPage() {
  let health: SystemHealth | null = null;
  let error = '';
  try {
    health = await adminApi.getSystemHealth();
  } catch {
    error = 'Failed to fetch system health. The API may be unavailable.';
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">System Health</h1>

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
