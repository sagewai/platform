'use client';

import { useCallback, useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { AutopilotMission } from '@/utils/types';

export default function AutopilotCostPage() {
  const [missions, setMissions] = useState<AutopilotMission[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchMissions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await adminApi.listAutopilotMissions(100);
      setMissions(res.missions.filter((m) => m.status === 'completed'));
    } catch {
      // non-fatal
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMissions();
  }, [fetchMissions]);

  return (
    <div className="space-y-6" data-testid="autopilot-cost-page">
      <div>
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Cost
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Completed autopilot missions. Per-mission cost details are available on each mission page.
        </p>
      </div>

      {loading ? (
        <div className="animate-pulse space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 bg-bg-subtle rounded-lg" />
          ))}
        </div>
      ) : (
        <figure role="group" aria-label="Completed missions">
          <table className="w-full text-sm border-collapse">
            <caption className="sr-only">Completed autopilot missions</caption>
            <thead>
              <tr className="border-b border-border">
                <th scope="col" className="text-left py-2 px-3 text-text-secondary font-medium text-xs uppercase tracking-wide">
                  Blueprint
                </th>
                <th scope="col" className="text-left py-2 px-3 text-text-secondary font-medium text-xs uppercase tracking-wide">
                  Mode
                </th>
                <th scope="col" className="text-left py-2 px-3 text-text-secondary font-medium text-xs uppercase tracking-wide">
                  Finished
                </th>
              </tr>
            </thead>
            <tbody>
              {missions.length === 0 ? (
                <tr>
                  <td colSpan={3} className="py-6 text-center text-text-muted text-sm">
                    No completed missions yet.
                  </td>
                </tr>
              ) : (
                missions.map((m) => (
                  <tr key={m.id} className="border-b border-border/50 hover:bg-bg-subtle">
                    <td className="py-2 px-3 text-text-primary truncate max-w-xs">
                      {m.blueprint_title || m.id}
                    </td>
                    <td className="py-2 px-3 text-text-secondary text-xs">
                      {m.mode}
                    </td>
                    <td className="py-2 px-3 text-text-secondary text-xs">
                      {m.finished_at
                        ? new Date(m.finished_at).toLocaleString(undefined, {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })
                        : '—'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </figure>
      )}
    </div>
  );
}
