'use client';

import { useState } from 'react';
import { Card, Button, useToast } from '@/components/ui/legacy';
import { Wrench, CheckCircle } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { MaintenanceReport } from '@/utils/types';

export function MaintenancePanel() {
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<MaintenanceReport | null>(null);
  const { toast } = useToast();

  async function runMaintenance() {
    setRunning(true);
    try {
      const r = await adminApi.triggerContextMaintenance();
      setReport(r);
      toast('success', 'Maintenance cycle completed');
    } catch (e) {
      toast('error', `Maintenance failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <Card className="p-lg">
      <div className="flex items-start justify-between mb-md">
        <div>
          <h3 className="text-sm font-semibold mb-1">Adaptive Maintenance</h3>
          <p className="text-xs text-text-muted max-w-[28rem]">
            Run a lifecycle maintenance cycle to compress old chunks, archive stale documents,
            discard low-importance content, and refresh importance scores.
          </p>
        </div>
        <Button onClick={runMaintenance} disabled={running}>
          <Wrench size={14} className={`mr-1 ${running ? 'animate-spin' : ''}`} />
          {running ? 'Running...' : 'Run Maintenance'}
        </Button>
      </div>

      {report && (
        <div className="mt-md border-t border-white/10 pt-md">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle size={14} className="text-green-400" />
            <span className="text-xs font-medium text-green-400">Maintenance Complete</span>
            <span className="text-xs text-text-muted">({report.duration_ms.toFixed(0)}ms)</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-md">
            {[
              { label: 'Compressed', value: report.chunks_compressed },
              { label: 'Archived', value: report.documents_archived },
              { label: 'Discarded', value: report.chunks_discarded },
              { label: 'Refreshed', value: report.importance_refreshed },
            ].map((item) => (
              <div key={item.label}>
                <div className="text-xs text-text-muted mb-0.5">{item.label}</div>
                <div className="text-lg font-bold">{item.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
