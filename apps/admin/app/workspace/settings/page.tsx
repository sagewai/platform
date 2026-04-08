'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { Workspace } from '@/utils/types';
import { Card, Button, Skeleton, useToast } from '@sagecurator/ui';

export default function WorkspaceSettingsPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selected, setSelected] = useState<Workspace | null>(null);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();

  const fetchWorkspaces = useCallback(async () => {
    try {
      const data = await adminApi.listWorkspaces();
      setWorkspaces(data);
      if (data.length > 0 && !selected) {
        setSelected(data[0]);
        setName(data[0].name);
      }
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => { fetchWorkspaces(); }, [fetchWorkspaces]);

  async function handleSave() {
    if (!selected) return;
    setSaving(true);
    try {
      await adminApi.updateWorkspace(selected.id, name);
      toast('success', 'Workspace updated.');
      fetchWorkspaces();
    } catch {
      toast('error', 'Failed to update workspace.');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!selected || !confirm('Are you sure you want to delete this workspace? This cannot be undone.')) return;
    try {
      await adminApi.deleteWorkspace(selected.id);
      setSelected(null);
      setName('');
      toast('success', 'Workspace deleted.');
      fetchWorkspaces();
    } catch {
      toast('error', 'Failed to delete workspace.');
    }
  }

  if (loading) return <Skeleton lines={5} />;

  return (
    <div className="max-w-[640px] mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Workspace Settings</h1>

      {selected ? (
        <>
          <Card>
            <label className="block mb-4">
              <span className="text-[13px] font-medium text-text-secondary block mb-1.5">Workspace Name</span>
              <input value={name} onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface box-border" />
            </label>

            <label className="block mb-4">
              <span className="text-[13px] font-medium text-text-secondary block mb-1.5">Slug (read-only)</span>
              <input value={selected.slug} disabled
                className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-subtle text-text-muted box-border" />
            </label>

            <label className="block mb-5">
              <span className="text-[13px] font-medium text-text-secondary block mb-1.5">Workspace ID</span>
              <input value={selected.id} disabled
                className="w-full px-3 py-2 border border-border rounded-md text-xs bg-bg-subtle text-text-muted font-[family-name:var(--font-mono)] box-border" />
            </label>

            <Button onClick={handleSave} disabled={saving}>
              {saving ? 'Saving...' : 'Save Changes'}
            </Button>
          </Card>

          <Card className="mt-lg border-error/30">
            <h3 className="mt-0 mb-2 text-[15px] font-semibold text-error">Danger Zone</h3>
            <p className="m-0 mb-4 text-[13px] text-text-muted">
              Permanently delete this workspace and all its data. This action cannot be undone.
            </p>
            <Button variant="secondary" className="text-error border-error" onClick={handleDelete}>
              Delete Workspace
            </Button>
          </Card>
        </>
      ) : (
        <Card>
          <p className="text-text-muted m-0">No workspace selected. Create one from the sidebar.</p>
        </Card>
      )}
    </div>
  );
}
