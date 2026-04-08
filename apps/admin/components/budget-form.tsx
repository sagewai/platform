'use client';

import { useState } from 'react';
import { Card, Button } from '@sagecurator/ui';

interface BudgetFormData {
  agent_name: string;
  max_daily_usd: number;
  max_monthly_usd: number;
  action: 'warn' | 'throttle' | 'stop';
  fallback_chain: string[];
}

interface BudgetFormProps {
  initial?: BudgetFormData;
  onSubmit: (data: BudgetFormData) => void;
  onCancel: () => void;
  isEdit?: boolean;
}

export function BudgetForm({ initial, onSubmit, onCancel, isEdit }: BudgetFormProps) {
  const [agentName, setAgentName] = useState(initial?.agent_name ?? '');
  const [maxDaily, setMaxDaily] = useState(initial?.max_daily_usd ?? 5);
  const [maxMonthly, setMaxMonthly] = useState(initial?.max_monthly_usd ?? 100);
  const [action, setAction] = useState<'warn' | 'throttle' | 'stop'>(initial?.action ?? 'warn');
  const [fallbackChain, setFallbackChain] = useState(
    initial?.fallback_chain?.join(', ') ?? '',
  );

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({
      agent_name: agentName,
      max_daily_usd: maxDaily,
      max_monthly_usd: maxMonthly,
      action,
      fallback_chain: fallbackChain
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    });
  }

  return (
    <Card className="mb-lg">
      <form onSubmit={handleSubmit}>
        <h3 className="mt-0 mb-5 text-base font-semibold font-[family-name:var(--font-heading)]">
          {isEdit ? 'Edit Budget Limit' : 'Create Budget Limit'}
        </h3>

        <div className="grid grid-cols-2 gap-md mb-md">
          <div>
            <label className="block mb-1 text-[13px] font-medium text-text-secondary">Agent Name</label>
            <input
              className="w-full px-3 py-2 border border-border rounded-md text-sm box-border bg-bg-surface"
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
              placeholder="e.g. scout-agent"
              required
              disabled={isEdit}
            />
          </div>
          <div>
            <label className="block mb-1 text-[13px] font-medium text-text-secondary">Action on Exceed</label>
            <select
              className="w-full px-3 py-2 border border-border rounded-md text-sm box-border bg-bg-surface"
              value={action}
              onChange={(e) => setAction(e.target.value as 'warn' | 'throttle' | 'stop')}
            >
              <option value="warn">Warn</option>
              <option value="throttle">Throttle (use fallback model)</option>
              <option value="stop">Stop</option>
            </select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-md mb-md">
          <div>
            <label className="block mb-1 text-[13px] font-medium text-text-secondary">Max Daily (USD)</label>
            <input
              className="w-full px-3 py-2 border border-border rounded-md text-sm box-border bg-bg-surface"
              type="number"
              step="0.01"
              min="0"
              value={maxDaily}
              onChange={(e) => setMaxDaily(parseFloat(e.target.value) || 0)}
              required
            />
          </div>
          <div>
            <label className="block mb-1 text-[13px] font-medium text-text-secondary">Max Monthly (USD)</label>
            <input
              className="w-full px-3 py-2 border border-border rounded-md text-sm box-border bg-bg-surface"
              type="number"
              step="0.01"
              min="0"
              value={maxMonthly}
              onChange={(e) => setMaxMonthly(parseFloat(e.target.value) || 0)}
              required
            />
          </div>
        </div>

        <div className="mb-5">
          <label className="block mb-1 text-[13px] font-medium text-text-secondary">Fallback Model Chain (comma-separated)</label>
          <input
            className="w-full px-3 py-2 border border-border rounded-md text-sm box-border bg-bg-surface"
            value={fallbackChain}
            onChange={(e) => setFallbackChain(e.target.value)}
            placeholder="e.g. gpt-4o-mini, gemini-2.0-flash"
          />
          <p className="mt-1 mb-0 text-xs text-text-muted">
            Ordered list of cheaper models to fall back to when budget is exceeded.
          </p>
        </div>

        <div className="flex gap-2">
          <Button type="submit">
            {isEdit ? 'Update Limit' : 'Create Limit'}
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}
