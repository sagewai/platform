'use client';

import { useState, useEffect, useCallback } from 'react';
import { Plus, Copy, Ban, Shield, AlertTriangle } from 'lucide-react';

interface HarnessKey {
  id: string;
  key_suffix: string;
  name: string;
  user_id: string;
  org_id: string;
  team_id: string | null;
  project_id: string | null;
  allowed_models: string[];
  max_budget_daily_usd: number | null;
  max_budget_monthly_usd: number | null;
  enabled: boolean;
  created_at: number;
  expires_at: number | null;
}

interface CreateKeyResponse {
  key_id: string;
  plaintext: string;
  name: string;
  key_suffix: string;
}

function statusBadge(key: HarnessKey) {
  if (!key.enabled) return { label: 'Revoked', cls: 'bg-red-500/10 text-red-400 border-red-500/20' };
  if (key.expires_at && key.expires_at * 1000 < Date.now()) {
    return { label: 'Expired', cls: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20' };
  }
  return { label: 'Active', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' };
}

function fmtBudget(v: number | null): string {
  return v === null || v === undefined ? '—' : `$${v.toFixed(2)}`;
}

export default function HarnessKeysPage() {
  const [keys, setKeys] = useState<HarnessKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createdSecret, setCreatedSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Create form state
  const [formName, setFormName] = useState('');
  const [formUserId, setFormUserId] = useState('');
  const [formDaily, setFormDaily] = useState('');
  const [formMonthly, setFormMonthly] = useState('');

  const fetchKeys = useCallback(() => {
    setLoading(true);
    fetch('/api/v1/harness/keys')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data: HarnessKey[]) => setKeys(data))
      .catch(() => setKeys([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  const handleRevoke = async (id: string) => {
    await fetch(`/api/v1/harness/keys/${id}`, { method: 'DELETE' });
    fetchKeys();
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const res = await fetch('/api/v1/harness/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: formName,
          user_id: formUserId,
          ...(formDaily ? { max_budget_daily_usd: parseFloat(formDaily) } : {}),
          ...(formMonthly ? { max_budget_monthly_usd: parseFloat(formMonthly) } : {}),
        }),
      });
      if (!res.ok) throw new Error(`create failed: ${res.status}`);
      const created: CreateKeyResponse = await res.json();
      setCreatedSecret(created.plaintext);
      setShowCreate(false);
      setFormName('');
      setFormUserId('');
      setFormDaily('');
      setFormMonthly('');
      fetchKeys();
    } catch {
      /* surface nothing extra; modal stays open on failure */
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = () => {
    if (!createdSecret) return;
    navigator.clipboard?.writeText(createdSecret)?.then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    })?.catch(() => {});
  };

  return (
    <div className="mx-auto max-w-[72rem] space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100">Harness Keys</h1>
          <p className="mt-1 text-sm text-zinc-400">
            API keys for authenticating AI tools with the harness proxy.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-500 transition"
        >
          <Plus className="w-4 h-4" />
          Create Key
        </button>
      </div>

      {/* Created key secret — shown once */}
      {createdSecret && (
        <div className="bg-amber-500/5 border border-amber-500/30 rounded-xl p-4 flex gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-amber-300">Save this key — it will not be shown again</p>
            <div className="flex items-center gap-2 mt-2">
              <code className="flex-1 bg-black/30 px-3 py-2 rounded font-mono text-sm break-all text-zinc-200">
                {createdSecret}
              </code>
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 rounded-lg bg-zinc-800 px-3 py-2 text-xs text-zinc-200 hover:bg-zinc-700 transition"
              >
                <Copy className="w-3.5 h-3.5" /> {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>
          <button
            onClick={() => setCreatedSecret(null)}
            className="text-zinc-500 hover:text-zinc-300 text-lg leading-none"
            aria-label="Dismiss"
          >
            &times;
          </button>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 space-y-4">
          <h2 className="text-base font-medium text-zinc-200">Create Harness Key</h2>
          <div className="grid gap-4 max-w-md">
            <label className="block text-sm">
              <span className="text-zinc-400 block mb-1">Name</span>
              <input
                type="text"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. alice-dev-key"
                className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-400 block mb-1">User ID</span>
              <input
                type="text"
                value={formUserId}
                onChange={(e) => setFormUserId(e.target.value)}
                placeholder="e.g. alice"
                className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                <span className="text-zinc-400 block mb-1">Daily budget ($, optional)</span>
                <input
                  type="number"
                  value={formDaily}
                  onChange={(e) => setFormDaily(e.target.value)}
                  placeholder="None"
                  min={0}
                  step="0.01"
                  className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
                />
              </label>
              <label className="block text-sm">
                <span className="text-zinc-400 block mb-1">Monthly budget ($, optional)</span>
                <input
                  type="number"
                  value={formMonthly}
                  onChange={(e) => setFormMonthly(e.target.value)}
                  placeholder="None"
                  min={0}
                  step="0.01"
                  className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
                />
              </label>
            </div>
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleCreate}
                disabled={creating || !formName.trim() || !formUserId.trim()}
                className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-500 transition disabled:opacity-50"
              >
                {creating ? 'Creating…' : 'Create Key'}
              </button>
              <button
                onClick={() => setShowCreate(false)}
                className="rounded-lg bg-zinc-800 px-4 py-2 text-sm text-zinc-300 hover:bg-zinc-700 transition"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Info banner */}
      <div className="bg-blue-500/5 border border-blue-500/20 rounded-xl p-4 flex gap-3">
        <Shield className="w-5 h-5 text-blue-400 shrink-0 mt-0.5" />
        <div className="text-sm text-blue-300">
          <p className="font-medium">How keys work</p>
          <p className="text-blue-400/70 mt-1">
            Each key is shown <strong>once</strong> at creation. Configure it as{' '}
            <code className="bg-blue-500/10 px-1 rounded text-xs">ANTHROPIC_API_KEY</code>{' '}
            in Claude Code or as the API key in Cursor/Copilot settings.
          </p>
        </div>
      </div>

      {/* Keys table */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-6 text-sm text-zinc-500">Loading keys…</div>
        ) : keys.length === 0 ? (
          <div className="p-6 text-sm text-zinc-500">
            No harness keys yet. Create one to authenticate an AI coding tool with the proxy.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
                <th className="p-4">Name</th>
                <th className="p-4">User</th>
                <th className="p-4">Key</th>
                <th className="p-4">Budget (Daily / Monthly)</th>
                <th className="p-4">Status</th>
                <th className="p-4">Created</th>
                <th className="p-4">Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const status = statusBadge(k);
                return (
                  <tr key={k.id} className={`border-b border-zinc-800/50 ${!k.enabled ? 'opacity-50' : ''}`}>
                    <td className="p-4">
                      <div className="font-medium text-zinc-200">{k.name || '(unnamed)'}</div>
                      <div className="text-[10px] text-zinc-500 mt-0.5">
                        {k.team_id ? `${k.org_id} / ${k.team_id}` : k.org_id}
                      </div>
                    </td>
                    <td className="p-4 text-zinc-300">{k.user_id || '—'}</td>
                    <td className="p-4">
                      <code className="bg-zinc-800 px-2 py-0.5 rounded text-xs text-zinc-400">
                        sk-harness-...{k.key_suffix}
                      </code>
                    </td>
                    <td className="p-4 text-zinc-400 text-xs">
                      {fmtBudget(k.max_budget_daily_usd)} / {fmtBudget(k.max_budget_monthly_usd)}
                    </td>
                    <td className="p-4">
                      <span className={`inline-block px-2 py-0.5 text-xs rounded-full border ${status.cls}`}>
                        {status.label}
                      </span>
                    </td>
                    <td className="p-4 text-xs text-zinc-500">
                      {k.created_at ? new Date(k.created_at * 1000).toLocaleDateString() : '—'}
                    </td>
                    <td className="p-4">
                      {k.enabled && (
                        <button
                          onClick={() => handleRevoke(k.id)}
                          className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300"
                        >
                          <Ban className="w-3 h-3" /> Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
