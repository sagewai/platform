'use client';

import { useState, useEffect, useCallback } from 'react';
import { Badge, Card, Button, EmptyState } from '@sagecurator/ui';
import { Shield, Plus, Pencil, Trash2, ToggleLeft, ToggleRight } from 'lucide-react';
const DEMO_POLICIES = [
  {
    id: 'pol-1',
    name: 'acme-default',
    description: 'Default routing for all Acme developers',
    scope: { org_id: 'acme-corp' },
    priority: 0,
    force_model: null,
    max_tier: null,
    tier_overrides: {},
    blocked_models: [],
    allow_override: true,
    enabled: true,
  },
  {
    id: 'pol-2',
    name: 'intern-cap',
    description: 'Interns capped at Sonnet — no Opus access',
    scope: { org_id: 'acme-corp', user_id: 'bob' },
    priority: 10,
    force_model: null,
    max_tier: 'medium',
    tier_overrides: {},
    blocked_models: ['claude-opus-4-6'],
    allow_override: false,
    enabled: true,
  },
  {
    id: 'pol-3',
    name: 'cost-saver',
    description: 'Force Haiku for the QA team',
    scope: { org_id: 'acme-corp', team_id: 'qa' },
    priority: 5,
    force_model: 'claude-haiku-4-5-20251001',
    max_tier: null,
    tier_overrides: {},
    blocked_models: [],
    allow_override: false,
    enabled: true,
  },
  {
    id: 'pol-4',
    name: 'deprecated-rule',
    description: 'Old policy — disabled',
    scope: { org_id: 'acme-corp' },
    priority: 0,
    force_model: 'gpt-3.5-turbo',
    max_tier: null,
    tier_overrides: {},
    blocked_models: [],
    allow_override: true,
    enabled: false,
  },
];

function scopeBadges(scope: Record<string, string | undefined>) {
  const badges = [];
  if (scope.org_id) badges.push({ label: `org:${scope.org_id}`, color: 'bg-blue-500/10 text-blue-400 border-blue-500/20' });
  if (scope.team_id) badges.push({ label: `team:${scope.team_id}`, color: 'bg-purple-500/10 text-purple-400 border-purple-500/20' });
  if (scope.project_id) badges.push({ label: `project:${scope.project_id}`, color: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20' });
  if (scope.user_id) badges.push({ label: `user:${scope.user_id}`, color: 'bg-amber-500/10 text-amber-400 border-amber-500/20' });
  return badges;
}

export default function HarnessPoliciesPage() {
  const [policies, setPolicies] = useState(DEMO_POLICIES);
  const [loading, setLoading] = useState(true);

  const fetchPolicies = useCallback(() => {
    fetch('/api/v1/harness/policies')
      .then(r => r.ok ? r.json() : Promise.reject())
      .then((data) => { if (data.length > 0) setPolicies(data); })
      .catch(() => { /* keep demo data */ })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchPolicies(); }, [fetchPolicies]);

  const handleDelete = async (id: string) => {
    await fetch(`/api/v1/harness/policies/${id}`, { method: 'DELETE' });
    fetchPolicies();
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
        {/* TODO: wire create modal */}
        <button className="flex items-center gap-2 rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-500 transition">
          <Plus className="w-4 h-4" />
          Create Policy
        </button>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
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
                    <button className="p-1 text-zinc-500 hover:text-zinc-300"><Pencil className="w-3.5 h-3.5" /></button>
                    <button className="p-1 text-zinc-500 hover:text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
