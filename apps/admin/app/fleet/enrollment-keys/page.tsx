'use client';

import { useState, useCallback, useEffect } from 'react';
import { Badge, Card, Button, EmptyState, Skeleton, useToast } from '@/components/ui/legacy';
import { Plus, Copy, Key, AlertTriangle } from 'lucide-react';
import type { FleetEnrollmentKey, FleetEnrollmentKeyCreate } from '@/utils/types';
import { adminApi } from '@/utils/api';

function getKeyStatus(key: FleetEnrollmentKey): { label: string; variant: 'success' | 'error' | 'warning' | 'default' } {
  if (key.revoked) return { label: 'revoked', variant: 'error' };
  if (key.expires_at && new Date(key.expires_at) < new Date()) return { label: 'expired', variant: 'default' };
  if (key.max_uses !== null && key.current_uses >= key.max_uses) return { label: 'exhausted', variant: 'warning' };
  return { label: 'active', variant: 'success' };
}

function formatDate(iso: string | null): string {
  if (!iso) return '--';
  return new Date(iso).toLocaleDateString();
}

export default function EnrollmentKeysPage() {
  const [keys, setKeys] = useState<FleetEnrollmentKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [createdKeySecret, setCreatedKeySecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [creating, setCreating] = useState(false);
  const { toast } = useToast();

  // Create form state
  const [formName, setFormName] = useState('');
  const [formMaxUses, setFormMaxUses] = useState('');
  const [formExpiresDays, setFormExpiresDays] = useState('');
  const [formPools, setFormPools] = useState('');
  const [formModels, setFormModels] = useState('');

  const fetchKeys = useCallback(async () => {
    try {
      const data = await adminApi.listEnrollmentKeys();
      setKeys(data.keys);
    } catch {
      toast('error', 'Failed to load enrollment keys');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    try {
      const payload: FleetEnrollmentKeyCreate = {
        name: formName || 'Untitled Key',
        ...(formMaxUses ? { max_uses: parseInt(formMaxUses, 10) } : {}),
        ...(formExpiresDays
          ? { expires_at: new Date(Date.now() + parseInt(formExpiresDays, 10) * 86400000).toISOString() }
          : {}),
        ...(formPools
          ? { allowed_pools: formPools.split(',').map((s) => s.trim()).filter(Boolean) }
          : {}),
        ...(formModels
          ? { allowed_models: formModels.split(',').map((s) => s.trim()).filter(Boolean) }
          : {}),
      };
      const created = await adminApi.createEnrollmentKey(payload);
      setCreatedKeySecret(created.raw_key ?? null);
      setShowCreate(false);
      setFormName('');
      setFormMaxUses('');
      setFormExpiresDays('');
      setFormPools('');
      setFormModels('');
      toast('success', 'Enrollment key created');
      await fetchKeys();
    } catch {
      toast('error', 'Failed to create enrollment key');
    } finally {
      setCreating(false);
    }
  }, [formName, formMaxUses, formExpiresDays, formPools, formModels, fetchKeys, toast]);

  async function handleRevoke(id: string) {
    try {
      await adminApi.revokeEnrollmentKey(id);
      setKeys((prev) => prev.map((k) => (k.id === id ? { ...k, revoked: true } : k)));
      toast('success', 'Enrollment key revoked');
    } catch {
      toast('error', 'Failed to revoke enrollment key');
    }
  }

  function handleCopyKey() {
    if (createdKeySecret) {
      navigator.clipboard?.writeText(createdKeySecret)?.then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })?.catch(() => {});
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
            Enrollment Keys
          </h1>
          <p className="mt-0 text-sm text-text-secondary">
            Create and manage keys that workers use to register with the fleet.
          </p>
        </div>
        <Button variant="primary" onClick={() => setShowCreate(true)}>
          <Plus size={14} className="mr-1" />
          Create Key
        </Button>
      </div>

      {/* Created key secret display */}
      {createdKeySecret && (
        <Card className="mb-md !border-amber-500/30 !bg-amber-500/5">
          <div className="flex items-start gap-3">
            <AlertTriangle size={18} className="text-amber-500 mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-amber-500 m-0 mb-2">
                Save this key -- it will not be shown again
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-black/30 rounded font-mono text-sm break-all">
                  {createdKeySecret}
                </code>
                <Button variant="secondary" size="sm" onClick={handleCopyKey}>
                  <Copy size={14} className="mr-1" />
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
            </div>
            <button
              onClick={() => setCreatedKeySecret(null)}
              className="text-text-muted hover:text-text-primary text-lg leading-none cursor-pointer bg-transparent border-0"
              aria-label="Dismiss"
            >
              &times;
            </button>
          </div>
        </Card>
      )}

      {/* Create dialog */}
      {showCreate && (
        <Card className="mb-md">
          <h3 className="text-base font-semibold m-0 mb-4">Create Enrollment Key</h3>
          <div className="grid gap-4 max-w-[28rem]">
            <label className="block text-sm">
              <span className="text-text-muted block mb-1">Name *</span>
              <input
                type="text"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. GPU Cluster Onboarding"
                className="w-full px-3 py-2 border border-border rounded text-sm bg-bg-surface"
              />
            </label>
            <label className="block text-sm">
              <span className="text-text-muted block mb-1">Max Uses (optional)</span>
              <input
                type="number"
                value={formMaxUses}
                onChange={(e) => setFormMaxUses(e.target.value)}
                placeholder="Unlimited"
                min={1}
                className="w-full px-3 py-2 border border-border rounded text-sm bg-bg-surface"
              />
            </label>
            <label className="block text-sm">
              <span className="text-text-muted block mb-1">Expires In (days, optional)</span>
              <input
                type="number"
                value={formExpiresDays}
                onChange={(e) => setFormExpiresDays(e.target.value)}
                placeholder="Never"
                min={1}
                className="w-full px-3 py-2 border border-border rounded text-sm bg-bg-surface"
              />
            </label>
            <label className="block text-sm">
              <span className="text-text-muted block mb-1">Allowed Pools (comma-separated, optional)</span>
              <input
                type="text"
                value={formPools}
                onChange={(e) => setFormPools(e.target.value)}
                placeholder="Any pool"
                className="w-full px-3 py-2 border border-border rounded text-sm bg-bg-surface"
              />
            </label>
            <label className="block text-sm">
              <span className="text-text-muted block mb-1">Allowed Models (comma-separated, optional)</span>
              <input
                type="text"
                value={formModels}
                onChange={(e) => setFormModels(e.target.value)}
                placeholder="Any model"
                className="w-full px-3 py-2 border border-border rounded text-sm bg-bg-surface"
              />
            </label>
            <div className="flex gap-2 pt-2">
              <Button variant="primary" onClick={handleCreate} disabled={!formName.trim() || creating}>
                {creating ? 'Creating…' : 'Create Key'}
              </Button>
              <Button variant="secondary" onClick={() => setShowCreate(false)}>
                Cancel
              </Button>
            </div>
          </div>
        </Card>
      )}

      {/* Keys table */}
      <Card>
        {loading ? (
          <Skeleton lines={4} />
        ) : keys.length === 0 ? (
          <EmptyState
            title="No Enrollment Keys"
            description="Create an enrollment key to allow workers to register with your fleet."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-border">
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Name
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Status
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Uses
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Expires
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Pools
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Models
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => {
                  const status = getKeyStatus(key);
                  return (
                    <tr
                      key={key.id}
                      className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors"
                    >
                      <td className="py-2.5 px-3">
                        <div className="flex items-center gap-2">
                          <Key size={14} className="text-text-muted shrink-0" />
                          <div>
                            <div className="font-medium">{key.name}</div>
                            <div className="text-[11px] text-text-muted">{key.id}</div>
                          </div>
                        </div>
                      </td>
                      <td className="py-2.5 px-3">
                        <Badge variant={status.variant}>{status.label}</Badge>
                      </td>
                      <td className="py-2.5 px-3 text-[13px]">
                        {key.current_uses}
                        {key.max_uses !== null ? ` / ${key.max_uses}` : ' (unlimited)'}
                      </td>
                      <td className="py-2.5 px-3 text-[13px] text-text-muted">
                        {formatDate(key.expires_at)}
                      </td>
                      <td className="py-2.5 px-3">
                        {key.allowed_pools.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {key.allowed_pools.map((p) => (
                              <span
                                key={p}
                                className="inline-block px-1.5 py-0.5 text-[11px] bg-bg-subtle border border-border rounded"
                              >
                                {p}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-[13px] text-text-muted">Any</span>
                        )}
                      </td>
                      <td className="py-2.5 px-3">
                        {key.allowed_models.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {key.allowed_models.map((m) => (
                              <span
                                key={m}
                                className="inline-block px-1.5 py-0.5 text-[11px] bg-bg-subtle border border-border rounded"
                              >
                                {m}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-[13px] text-text-muted">Any</span>
                        )}
                      </td>
                      <td className="py-2.5 px-3">
                        {!key.revoked && (
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => handleRevoke(key.id)}
                          >
                            Revoke
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
