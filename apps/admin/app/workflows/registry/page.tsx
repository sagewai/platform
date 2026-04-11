'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { SavedWorkflow } from '@/utils/types';
import Link from 'next/link';
import { Badge, Button, Skeleton, EmptyState } from '@/components/ui/legacy';
import { ResponsiveTable } from '@/components/responsive-table';
import { Trash2, Edit, History, Plus } from 'lucide-react';

export const dynamic = 'force-dynamic';

export default function WorkflowRegistryPage() {
  const [workflows, setWorkflows] = useState<SavedWorkflow[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);

  const fetchWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const result = await adminApi.listSavedWorkflows({
        search: search || undefined,
        limit: 50,
      });
      setWorkflows(result.items);
      setTotal(result.total);
    } catch {
      setWorkflows([]);
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    const timeout = setTimeout(fetchWorkflows, 300);
    return () => clearTimeout(timeout);
  }, [fetchWorkflows]);

  async function handleDelete(id: string, name: string) {
    if (!confirm(`Deactivate workflow "${name}"?`)) return;
    try {
      await adminApi.deleteSavedWorkflow(id);
      fetchWorkflows();
    } catch {
      // ignore
    }
  }

  return (
    <div className="space-y-lg">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Workflow Registry</h1>
          <p className="text-text-muted text-sm mt-1">
            {total} saved workflow{total !== 1 ? 's' : ''}
          </p>
        </div>
        <Link href="/workflows">
          <Button variant="primary" className="flex items-center gap-2">
            <Plus className="w-4 h-4" />
            New Workflow
          </Button>
        </Link>
      </div>

      <div className="flex gap-3">
        <input
          placeholder="Search workflows..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-2 border border-border rounded-md text-sm w-[280px] bg-bg-surface"
        />
      </div>

      {loading ? (
        <Skeleton lines={5} />
      ) : workflows.length === 0 ? (
        <EmptyState
          title="No Saved Workflows"
          description="Save a workflow from the editor to see it here."
        />
      ) : (
        <div className="bg-bg-surface rounded-lg border border-border overflow-hidden">
          <ResponsiveTable
            columns={[
              { key: 'name', label: 'Name' },
              { key: 'description', label: 'Description' },
              { key: 'version', label: 'Version' },
              { key: 'status', label: 'Status' },
              { key: 'updated', label: 'Updated' },
              { key: 'actions', label: '' },
            ]}
            rows={workflows.map((wf) => ({
              name: (
                <span className="font-medium font-[family-name:var(--font-mono)] text-sm">
                  {wf.name}
                </span>
              ),
              description: (
                <span className="text-text-muted text-sm truncate max-w-[300px] block">
                  {wf.description || '—'}
                </span>
              ),
              version: (
                <Badge variant="default">v{wf.version}</Badge>
              ),
              status: (
                <Badge variant={wf.is_active ? 'success' : 'default'}>
                  {wf.is_active ? 'Active' : 'Inactive'}
                </Badge>
              ),
              updated: (
                <span className="text-text-muted text-xs">
                  {wf.updated_at ? new Date(wf.updated_at * 1000).toLocaleString() : '—'}
                </span>
              ),
              actions: (
                <div className="flex items-center gap-1">
                  <Link href={`/workflows?load=${encodeURIComponent(wf.name)}`}>
                    <Button variant="ghost" size="sm" title="Edit in builder">
                      <Edit className="w-3.5 h-3.5" />
                    </Button>
                  </Link>
                  <Button
                    variant="ghost"
                    size="sm"
                    title="Delete"
                    onClick={() => handleDelete(wf.id, wf.name)}
                  >
                    <Trash2 className="w-3.5 h-3.5 text-red-500" />
                  </Button>
                </div>
              ),
            }))}
          />
        </div>
      )}
    </div>
  );
}
