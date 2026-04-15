'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { adminApi } from '@/utils/api';
import { authSSE } from '@/utils/auth';
import { TVLiveNumbers } from '@/components/premium/tv-live-numbers';
import { TVEventFeed, type TVEvent } from '@/components/premium/tv-event-feed';
import { TVStatusBoard } from '@/components/premium/tv-status-board';
import { TVCostTracker } from '@/components/premium/tv-cost-tracker';
import { TVControls } from '@/components/premium/tv-controls';
import { TVAlertOverlay } from '@/components/premium/tv-alert-overlay';
import type { QueueStats, WorkerInfo } from '@/utils/types';

const TOTAL_SCREENS = 4;
const API_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL ?? 'http://localhost:8000/admin';

function TVDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const cycleSpeed = Number(searchParams.get('cycle') || '30');
  const queueAlertThreshold = Number(searchParams.get('queue_alert') || '100');
  const errorAlertThreshold = Number(searchParams.get('error_alert') || '10');

  const [currentScreen, setCurrentScreen] = useState(0);
  const [isPinned, setIsPinned] = useState(false);
  const [stats, setStats] = useState<QueueStats | null>(null);
  const [workers, setWorkers] = useState<WorkerInfo[]>([]);
  const [events, setEvents] = useState<TVEvent[]>([]);
  const [dlqCount, setDlqCount] = useState(0);
  const [costData, setCostData] = useState<{ totalCost: number; costByModel: Record<string, number> }>({
    totalCost: 0,
    costByModel: {},
  });

  const eventIdRef = useRef(0);

  // Poll stats every 5s
  useEffect(() => {
    const poll = async () => {
      try {
        const [s, w, dlq, costs] = await Promise.all([
          adminApi.getWorkflowStats(),
          adminApi.listWorkers(),
          adminApi.listDLQ({ limit: 1 }),
          adminApi.getCosts(),
        ]);
        setStats(s);
        setWorkers(w);
        setDlqCount(Array.isArray(dlq) ? dlq.length : 0);
        setCostData({
          totalCost: costs.total_cost_usd ?? 0,
          costByModel: costs.by_model ?? {},
        });
      } catch {
        // API unavailable — keep last state
      }
    };

    poll();
    const interval = setInterval(poll, 5000);
    return () => clearInterval(interval);
  }, []);

  // SSE subscription for live events
  useEffect(() => {
    const controller = authSSE(
      `${API_BASE}/workflow-events/stream`,
      (type, data) => {
        const now = new Date();
        const ts = now.toLocaleTimeString('en-US', { hour12: false });
        const id = String(++eventIdRef.current);
        const message =
          (data.workflow_name as string) || (data.error as string) || (data.message as string) || type.replace(/_/g, ' ');

        setEvents((prev) => {
          const next = [...prev, { id, type, message, timestamp: ts }];
          return next.slice(-50);
        });
      },
      { reconnect: true },
    );
    return () => controller.abort();
  }, []);

  // Auto-cycle screens
  useEffect(() => {
    if (isPinned) return;
    const timer = setInterval(() => {
      setCurrentScreen((s) => (s + 1) % TOTAL_SCREENS);
    }, cycleSpeed * 1000);
    return () => clearInterval(timer);
  }, [isPinned, cycleSpeed]);

  // Keyboard controls
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      switch (e.key) {
        case 'Escape':
          router.push('/');
          break;
        case 'ArrowLeft':
          setCurrentScreen((s) => (s - 1 + TOTAL_SCREENS) % TOTAL_SCREENS);
          break;
        case 'ArrowRight':
          setCurrentScreen((s) => (s + 1) % TOTAL_SCREENS);
          break;
        case ' ':
          e.preventDefault();
          setIsPinned((p) => !p);
          break;
      }
    },
    [router],
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div className="h-screen w-screen overflow-hidden bg-[#0A1628] relative">
      {/* Screen content */}
      <div className="h-full w-full">
        {currentScreen === 0 && <TVLiveNumbers stats={stats} workers={workers} />}
        {currentScreen === 1 && <TVEventFeed events={events} />}
        {currentScreen === 2 && <TVStatusBoard stats={stats} workers={workers} />}
        {currentScreen === 3 && <TVCostTracker data={costData} />}
      </div>

      {/* Alerts */}
      <TVAlertOverlay
        stats={stats}
        workers={workers}
        dlqCount={dlqCount}
        queueAlertThreshold={queueAlertThreshold}
        errorAlertThreshold={errorAlertThreshold}
      />

      {/* Controls */}
      <TVControls
        currentScreen={currentScreen}
        totalScreens={TOTAL_SCREENS}
        isPinned={isPinned}
        cycleSpeed={cycleSpeed}
        onTogglePin={() => setIsPinned((p) => !p)}
        onExit={() => router.push('/')}
      />
    </div>
  );
}

export default function TVPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-screen bg-[#0A1628]">
          <div className="text-white/40 text-lg">Loading...</div>
        </div>
      }
    >
      <TVDashboard />
    </Suspense>
  );
}
