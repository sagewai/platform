'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { ContextConflict } from '@/utils/types';
import { MaintenancePanel } from '@/components/maintenance-panel';
import { ConflictTable } from '@/components/conflict-table';

export default function LifecyclePage() {
  const [conflicts, setConflicts] = useState<ContextConflict[]>([]);
  const [loadingConflicts, setLoadingConflicts] = useState(true);

  async function loadConflicts() {
    setLoadingConflicts(true);
    try {
      const data = await adminApi.listContextConflicts();
      setConflicts(data.conflicts);
    } catch {
      setConflicts([]);
    } finally {
      setLoadingConflicts(false);
    }
  }

  useEffect(() => { loadConflicts(); }, []);

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-lg">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1">Lifecycle Management</h1>
        <p className="text-text-muted text-sm">
          Maintain knowledge freshness, resolve content conflicts, and optimize storage through adaptive lifecycle management.
        </p>
      </div>

      <div className="space-y-lg">
        <MaintenancePanel />

        <div>
          <h2 className="text-sm font-semibold mb-md uppercase tracking-wide text-text-muted">
            Detected Conflicts ({conflicts.length})
          </h2>
          <ConflictTable
            conflicts={conflicts}
            loading={loadingConflicts}
            onResolved={loadConflicts}
          />
        </div>
      </div>
    </div>
  );
}
