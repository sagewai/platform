'use client';

import { useState, useEffect, useCallback } from 'react';
import { Key, Plus, Copy, Ban, Shield } from 'lucide-react';
const DEMO_KEYS = [
  {
    id: 'k-001',
    name: 'alice-dev-key',
    user_id: 'alice',
    org_id: 'acme-corp',
    team_id: 'engineering',
    suffix: 'a3f7',
    budget_daily: 5.0,
    budget_monthly: 50.0,
    enabled: true,
    created: '2026-03-28T10:00:00Z',
    expires: null,
  },
  {
    id: 'k-002',
    name: 'bob-intern-key',
    user_id: 'bob',
    org_id: 'acme-corp',
    team_id: 'engineering',
    suffix: 'b2e1',
    budget_daily: 1.0,
    budget_monthly: 10.0,
    enabled: true,
    created: '2026-03-29T14:00:00Z',
    expires: '2026-06-30T00:00:00Z',
  },
  {
    id: 'k-003',
    name: 'ci-pipeline-key',
    user_id: 'ci-bot',
    org_id: 'acme-corp',
    team_id: null,
    suffix: 'c9d4',
    budget_daily: 20.0,
    budget_monthly: 200.0,
    enabled: false,
    created: '2026-03-20T08:00:00Z',
    expires: null,
  },
];

function statusBadge(key: typeof DEMO_KEYS[0]) {
  if (!key.enabled) return { label: 'Revoked', cls: 'bg-red-500/10 text-red-400 border-red-500/20' };
  if (key.expires && new Date(key.expires) < new Date()) return { label: 'Expired', cls: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20' };
  return { label: 'Active', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' };
}

export default function HarnessKeysPage() {
  const [keys, setKeys] = useState(DEMO_KEYS);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchKeys = useCallback(() => {
    fetch('/api/v1/harness/keys')
      .then(r => r.ok ? r.json() : Promise.reject())
      .then((data) => { if (data.length > 0) setKeys(data); })
      .catch(() => { /* keep demo data */ })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchKeys(); }, [fetchKeys]);

  const handleRevoke = async (id: string) => {
    await fetch(`/api/v1/harness/keys/${id}`, { method: 'DELETE' });
    fetchKeys();
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
            {DEMO_KEYS.map((k) => {
              const status = statusBadge(k);
              return (
                <tr key={k.id} className={`border-b border-zinc-800/50 ${!k.enabled ? 'opacity-50' : ''}`}>
                  <td className="p-4">
                    <div className="font-medium text-zinc-200">{k.name}</div>
                    <div className="text-[10px] text-zinc-500 mt-0.5">
                      {k.team_id ? `${k.org_id} / ${k.team_id}` : k.org_id}
                    </div>
                  </td>
                  <td className="p-4 text-zinc-300">{k.user_id}</td>
                  <td className="p-4">
                    <code className="bg-zinc-800 px-2 py-0.5 rounded text-xs text-zinc-400">
                      sk-harness-...{k.suffix}
                    </code>
                  </td>
                  <td className="p-4 text-zinc-400 text-xs">
                    ${k.budget_daily.toFixed(2)} / ${k.budget_monthly.toFixed(2)}
                  </td>
                  <td className="p-4">
                    <span className={`inline-block px-2 py-0.5 text-xs rounded-full border ${status.cls}`}>
                      {status.label}
                    </span>
                  </td>
                  <td className="p-4 text-xs text-zinc-500">
                    {new Date(k.created).toLocaleDateString()}
                  </td>
                  <td className="p-4">
                    {k.enabled && (
                      <button className="flex items-center gap-1 text-xs text-red-400 hover:text-red-300">
                        <Ban className="w-3 h-3" /> Revoke
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
