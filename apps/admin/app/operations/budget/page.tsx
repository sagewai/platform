'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { BudgetLimit } from '@/utils/types';
import { BudgetForm } from '@/components/budget-form';
import { FallbackChainEditor } from '@/components/fallback-chain-editor';
import { Card, Button, Badge, Skeleton, EmptyState } from '@/components/ui/legacy';

export default function BudgetManagerPage() {
  const [limits, setLimits] = useState<BudgetLimit[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingLimit, setEditingLimit] = useState<BudgetLimit | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchLimits = useCallback(async () => {
    try {
      const data = await adminApi.listBudgetLimits();
      setLimits(data);
      setError(null);
    } catch {
      setError('Failed to load budget limits. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLimits();
  }, [fetchLimits]);

  async function handleCreate(data: BudgetLimit) {
    try {
      await adminApi.createBudgetLimit(data);
      setShowForm(false);
      fetchLimits();
    } catch {
      setError('Failed to create budget limit.');
    }
  }

  async function handleUpdate(data: BudgetLimit) {
    try {
      await adminApi.updateBudgetLimit(data.agent_name, data);
      setEditingLimit(null);
      fetchLimits();
    } catch {
      setError('Failed to update budget limit.');
    }
  }

  async function handleDelete(agentName: string) {
    try {
      await adminApi.deleteBudgetLimit(agentName);
      if (selectedAgent === agentName) setSelectedAgent(null);
      fetchLimits();
    } catch {
      setError('Failed to delete budget limit.');
    }
  }

  function handleFallbackChainChange(agentName: string, chain: string[]) {
    const limit = limits.find((l) => l.agent_name === agentName);
    if (!limit) return;
    handleUpdate({ ...limit, fallback_chain: chain });
  }

  const selectedLimit = limits.find((l) => l.agent_name === selectedAgent);

  const actionVariant = (action: string): 'error' | 'warning' | 'success' =>
    action === 'stop' ? 'error' : action === 'throttle' ? 'warning' : 'success';

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex justify-between items-center mb-lg">
        <div>
          <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">Budget Manager</h1>
          <p className="mt-0 text-sm text-text-secondary">
            Set spending limits and model fallback chains for agents.
          </p>
        </div>
        {!showForm && !editingLimit && (
          <Button onClick={() => setShowForm(true)}>+ New Limit</Button>
        )}
      </div>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <BudgetForm onSubmit={handleCreate} onCancel={() => setShowForm(false)} />
      )}

      {/* Edit form */}
      {editingLimit && (
        <BudgetForm
          initial={editingLimit}
          onSubmit={handleUpdate}
          onCancel={() => setEditingLimit(null)}
          isEdit
        />
      )}

      {/* Budget limits table */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Budget Limits</h3>

        {loading ? (
          <Skeleton lines={5} />
        ) : limits.length === 0 ? (
          <EmptyState
            title="No Budget Limits"
            description='No budget limits configured. Click "+ New Limit" to create one.'
          />
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Agent</th>
                <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Daily Limit</th>
                <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Monthly Limit</th>
                <th className="text-center py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Action</th>
                <th className="text-center py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Fallback Chain</th>
                <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody>
              {limits.map((lim) => (
                <tr
                  key={lim.agent_name}
                  className={`border-b border-border last:border-0 cursor-pointer hover:bg-bg-subtle transition-colors ${selectedAgent === lim.agent_name ? 'bg-primary-light' : ''}`}
                  onClick={() =>
                    setSelectedAgent(
                      selectedAgent === lim.agent_name ? null : lim.agent_name,
                    )
                  }
                >
                  <td className="py-2.5 px-3 font-medium">{lim.agent_name}</td>
                  <td className="py-2.5 px-3 text-right">${lim.max_daily_usd.toFixed(2)}</td>
                  <td className="py-2.5 px-3 text-right">${lim.max_monthly_usd.toFixed(2)}</td>
                  <td className="py-2.5 px-3 text-center">
                    <Badge variant={actionVariant(lim.action)}>{lim.action}</Badge>
                  </td>
                  <td className="py-2.5 px-3 text-[13px] text-center text-text-muted">
                    {lim.fallback_chain.length > 0
                      ? lim.fallback_chain.join(' -> ')
                      : '--'}
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    <Button
                      variant="secondary"
                      onClick={(e: React.MouseEvent) => {
                        e.stopPropagation();
                        setEditingLimit(lim);
                      }}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="secondary"
                      className="ml-1 text-error border-error"
                      onClick={(e: React.MouseEvent) => {
                        e.stopPropagation();
                        handleDelete(lim.agent_name);
                      }}
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Fallback chain editor for selected agent */}
      {selectedLimit && (
        <FallbackChainEditor
          chain={selectedLimit.fallback_chain}
          onChange={(chain) => handleFallbackChainChange(selectedLimit.agent_name, chain)}
        />
      )}
    </div>
  );
}
