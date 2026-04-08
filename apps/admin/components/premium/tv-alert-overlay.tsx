'use client';

import { useEffect, useState } from 'react';
import type { QueueStats, WorkerInfo } from '@/utils/types';

interface Alert {
  id: string;
  type: 'critical' | 'error' | 'warning';
  message: string;
  createdAt: number;
}

interface Props {
  stats: QueueStats | null;
  workers: WorkerInfo[];
  dlqCount: number;
  queueAlertThreshold: number;
  errorAlertThreshold: number;
}

export function TVAlertOverlay({
  stats,
  workers,
  dlqCount,
  queueAlertThreshold,
  errorAlertThreshold,
}: Props) {
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    const now = Date.now();
    const newAlerts: Alert[] = [];

    // No active workers
    if (stats && workers.length === 0 && stats.total > 0) {
      newAlerts.push({
        id: 'no-workers',
        type: 'critical',
        message: 'NO ACTIVE WORKERS',
        createdAt: now,
      });
    }

    // DLQ non-empty
    if (dlqCount > 0) {
      newAlerts.push({
        id: 'dlq',
        type: 'error',
        message: `${dlqCount} failed workflow${dlqCount > 1 ? 's' : ''} awaiting review`,
        createdAt: now,
      });
    }

    // Queue depth high
    if (stats && stats.pending > queueAlertThreshold) {
      newAlerts.push({
        id: 'queue-depth',
        type: 'warning',
        message: `Queue depth at ${stats.pending} (threshold: ${queueAlertThreshold})`,
        createdAt: now,
      });
    }

    // High error rate
    if (stats && stats.total > 0) {
      const errorRate = (stats.failed / stats.total) * 100;
      if (errorRate > errorAlertThreshold) {
        newAlerts.push({
          id: 'error-rate',
          type: 'warning',
          message: `Error rate at ${errorRate.toFixed(1)}% (threshold: ${errorAlertThreshold}%)`,
          createdAt: now,
        });
      }
    }

    setAlerts(newAlerts);
  }, [stats, workers, dlqCount, queueAlertThreshold, errorAlertThreshold]);

  // Auto-dismiss after 10s
  useEffect(() => {
    if (alerts.length === 0) return;
    const timer = setTimeout(() => setAlerts([]), 10000);
    return () => clearTimeout(timer);
  }, [alerts]);

  const hasCritical = alerts.some((a) => a.type === 'critical');

  if (alerts.length === 0) return null;

  return (
    <div className="fixed inset-0 z-[100] pointer-events-none">
      {/* Full-screen critical overlay */}
      {hasCritical && (
        <div className="absolute inset-0 flex items-center justify-center bg-red-900/40 animate-pulse">
          <div className="text-6xl font-bold text-red-400 tracking-wide">
            {alerts.find((a) => a.type === 'critical')?.message}
          </div>
        </div>
      )}

      {/* Pulsing border for queue alerts */}
      {alerts.some((a) => a.id === 'queue-depth') && !hasCritical && (
        <div className="absolute inset-0 border-4 border-amber-400/60 animate-pulse rounded-lg" />
      )}

      {/* Banner alerts */}
      <div className="absolute top-0 left-0 right-0 flex flex-col items-center gap-2 p-4">
        {alerts
          .filter((a) => a.type !== 'critical')
          .map((alert) => (
            <div
              key={alert.id}
              className={`px-6 py-3 rounded-lg text-sm font-semibold shadow-lg animate-[slideDown_0.3s_ease-out] ${
                alert.type === 'error'
                  ? 'bg-red-600 text-white'
                  : 'bg-amber-500 text-black'
              }`}
            >
              {alert.message}
            </div>
          ))}
      </div>
    </div>
  );
}
