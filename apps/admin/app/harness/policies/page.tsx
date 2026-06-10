'use client';

import { useState, useEffect, useCallback } from 'react';
import { Plus, Trash2, ToggleLeft, ToggleRight } from 'lucide-react';

interface PolicyScope {
  org_id?: string | null;
  team_id?: string | null;
  project_id?: string | null;
  user_id?: string | null;
}

interface PolicyRule {
  id: string;
  name: string;
  description: string;
  scope: PolicyScope;
  priority: number;
  tier_overrides: Record<string, string>;
  blocked_models: string[];
  allowed_models: string[];
  max_tier: string | null;
  force_model: string | null;
  allow_override: boolean;
  enabled: boolean;
}

function scopeBadges(scope: PolicyScope) {
  const badges: { label: string; color: string }[] = [];
  if (scope.org_id) badges.push({ label: `org:${scope.org_id}`, color: 'bg-blue-500/10 text-blue-400 border-blue-500/20' });
  if (scope.team_id) badges.push({ label: `team:${scope.team_id}`, color: 'bg-purple-500/10 text-purple-400 border-purple-500/20' });
  if (scope.project_id) badges.push({ label: `project:${scope.project_id}`, color: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20' });
  if (scope.user_id) badges.push({ label: `user:${scope.user_id}`, color: 'bg-amber-500/10 text-amber-400 border-amber-500/20' });
  return badges;
}

export default function HarnessPoliciesPage() {
  const [policies, setPolicies] = useState<PolicyRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);

  // Create form state
  const [formName, setFormName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formOrgId, setFormOrgId] = useState('');
  const [formUserId, setFormUserId] = useState('');
  const [formPriority, setFormPriority] = useState('0');
  const [formMaxTier, setFormMaxTier] = useState('');
  const [formForceModel, setFormForceModel] = useState('');
  const [formBlockedModels, setFormBlockedModels] = useState('');
  const [formAllowOverride, setFormAllowOverride] = useState(true);

  const fetchPolicies = useCallback(() => {
    setLoading(true);
    fetch('/api/v1/harness/policies')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data: PolicyRule[]) => setPolicies(data))
      .catch(() => setPolicies([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchPolicies(); }, [fetchPolicies]);

  const handleDelete = async (id: string) => {
    await fetch(`/api/v1/harness/policies/${id}`, { method: 'DELETE' });
    fetchPolicies();
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const scope: PolicyScope = {};
      if (formOrgId.trim()) scope.org_id = formOrgId.trim();
      if (formUserId.trim()) scope.user_id = formUserId.trim();
      const res = await fetch('/api/v1/harness/policies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: formName,
          description: formDescription,
          scope,
          priority: parseInt(formPriority, 10) || 0,
          max_tier: formMaxTier || null,
          force_model: formForceModel.trim() || null,
          blocked_models: formBlockedModels
            ? formBlockedModels.split(',').map((s) => s.trim()).filter(Boolean)
            : [],
          allow_override: formAllowOverride,
          enabled: true,
        }),
      });
      if (!res.ok) throw new Error(`create failed: ${res.status}`);
      setShowCreate(false);
      setFormName('');
      setFormDescription('');
      setFormOrgId('');
      setFormUserId('');
      setFormPriority('0');
      setFormMaxTier('');
      setFormForceModel('');
      setFormBlockedModels('');
      setFormAllowOverride(true);
      fetchPolicies();
    } catch {
      /* modal stays open on failure */
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto max-w-[72rem] space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100">Routing Policies</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Control which models developers can use and how requests are routed.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-500 transition"
        >
          <Plus className="w-4 h-4" />
          Create Policy
        </button>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 space-y-4">
          <h2 className="text-base font-medium text-zinc-200">Create Routing Policy</h2>
          <div className="grid gap-4 max-w-lg">
            <label className="block text-sm">
              <span className="text-zinc-400 block mb-1">Name</span>
              <input
                type="text"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. intern-cap"
                className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-400 block mb-1">Description</span>
              <input
                type="text"
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                placeholder="What this policy does"
                className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                <span className="text-zinc-400 block mb-1">Scope: org_id (optional)</span>
                <input
                  type="text"
                  value={formOrgId}
                  onChange={(e) => setFormOrgId(e.target.value)}
                  placeholder="e.g. acme-corp"
                  className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
                />
              </label>
              <label className="block text-sm">
                <span className="text-zinc-400 block mb-1">Scope: user_id (optional)</span>
                <input
                  type="text"
                  value={formUserId}
                  onChange={(e) => setFormUserId(e.target.value)}
                  placeholder="e.g. bob"
                  className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
                />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                <span className="text-zinc-400 block mb-1">Priority</span>
                <input
                  type="number"
                  value={formPriority}
                  onChange={(e) => setFormPriority(e.target.value)}
                  className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
                />
              </label>
              <label className="block text-sm">
                <span className="text-zinc-400 block mb-1">Max tier (optional)</span>
                <select
                  value={formMaxTier}
                  onChange={(e) => setFormMaxTier(e.target.value)}
                  className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
                >
                  <option value="">Auto (no cap)</option>
                  <option value="simple">simple</option>
                  <option value="medium">medium</option>
                  <option value="complex">complex</option>
                </select>
              </label>
            </div>
            <label className="block text-sm">
              <span className="text-zinc-400 block mb-1">Force model (optional)</span>
              <input
                type="text"
                value={formForceModel}
                onChange={(e) => setFormForceModel(e.target.value)}
                placeholder="e.g. claude-haiku-4-5-20251001"
                className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-400 block mb-1">Blocked models (comma-separated, optional)</span>
              <input
                type="text"
                value={formBlockedModels}
                onChange={(e) => setFormBlockedModels(e.target.value)}
                placeholder="e.g. claude-opus-4-6"
                className="w-full rounded-lg bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100"
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-zinc-300">
              <input
                type="checkbox"
                checked={formAllowOverride}
                onChange={(e) => setFormAllowOverride(e.target.checked)}
              />
              Allow developers to override routing
            </label>
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleCreate}
                disabled={creating || !formName.trim()}
                className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-500 transition disabled:opacity-50"
              >
                {creating ? 'Creating…' : 'Create Policy'}
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

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        {loading ? (
          <div className="p-6 text-sm text-zinc-500">Loading policies…</div>
        ) : policies.length === 0 ? (
          <div className="p-6 text-sm text-zinc-500">
            No routing policies yet. Create one to control which models developers can use.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
                <th className="p-4">Name</th>
                <th className="p-4">Scope</th>
                <th className="p-4">Priority</th>
                <th className="p-4">Routing</th>
                <th className="p-4">Override</th>
                <th className="p-4">Status</th>
                <th className="p-4">Actions</th>
              </tr>
            </thead>
            <tbody>
              {policies.map((p) => (
                <tr key={p.id} className={`border-b border-zinc-800/50 ${!p.enabled ? 'opacity-50' : ''}`}>
                  <td className="p-4">
                    <div className="font-medium text-zinc-200">{p.name}</div>
                    <div className="text-xs text-zinc-500 mt-0.5">{p.description}</div>
                  </td>
                  <td className="p-4">
                    <div className="flex flex-wrap gap-1">
                      {scopeBadges(p.scope).map((b) => (
                        <span key={b.label} className={`inline-block px-2 py-0.5 text-[10px] rounded-full border ${b.color}`}>
                          {b.label}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="p-4 text-zinc-400">{p.priority}</td>
                  <td className="p-4">
                    {p.force_model ? (
                      <span className="text-xs text-orange-400">force: {p.force_model.split('-').pop()}</span>
                    ) : p.max_tier ? (
                      <span className="text-xs text-amber-400">max: {p.max_tier}</span>
                    ) : (
                      <span className="text-xs text-zinc-500">auto (by complexity)</span>
                    )}
                    {p.blocked_models.length > 0 && (
                      <div className="text-[10px] text-red-400 mt-0.5">
                        blocked: {p.blocked_models.length} model(s)
                      </div>
                    )}
                  </td>
                  <td className="p-4">
                    {p.allow_override ? (
                      <span className="text-xs text-emerald-400">Allowed</span>
                    ) : (
                      <span className="text-xs text-red-400">Blocked</span>
                    )}
                  </td>
                  <td className="p-4">
                    {p.enabled ? (
                      <span className="inline-flex items-center gap-1 text-xs text-emerald-400">
                        <ToggleRight className="w-3.5 h-3.5" /> Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs text-zinc-500">
                        <ToggleLeft className="w-3.5 h-3.5" /> Disabled
                      </span>
                    )}
                  </td>
                  <td className="p-4">
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleDelete(p.id)}
                        className="p-1 text-zinc-500 hover:text-red-400"
                        aria-label="Delete policy"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
