'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { RoutingRule, RouteTestResponse, AvailableModel } from '@/utils/types';
import { Card, Button, Skeleton, EmptyState } from '@sagecurator/ui';

export default function ModelRouterPage() {
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [models, setModels] = useState<AvailableModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Test panel
  const [query, setQuery] = useState('');
  const [defaultModel, setDefaultModel] = useState('');
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<RouteTestResponse | null>(null);

  useEffect(() => {
    Promise.all([adminApi.listRoutingRules(), adminApi.listAvailableModels()])
      .then(([r, m]) => {
        setRules(r);
        setModels(m);
        if (m.length > 0 && !defaultModel) setDefaultModel(m[0].id);
      })
      .catch(() => setError('Failed to load routing rules.'))
      .finally(() => setLoading(false));
  }, []);

  async function handleTest() {
    if (!query) return;
    setTesting(true);
    setResult(null);
    try {
      const data = await adminApi.testRoute(query, {}, defaultModel);
      setResult(data);
      setError(null);
    } catch {
      setError('Route test failed.');
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Model Router</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        View routing rules and test model selection for queries.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Routing rules */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Routing Rules</h3>
        {loading ? (
          <Skeleton lines={3} />
        ) : rules.length === 0 ? (
          <EmptyState title="No Rules" description="No routing rules configured." />
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Rule</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Target Model</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Condition</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Description</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.name} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                  <td className="py-2.5 px-3 font-medium">{r.name}</td>
                  <td className="py-2.5 px-3 text-[13px] font-[family-name:var(--font-mono)]">{r.target_model}</td>
                  <td className="py-2.5 px-3 text-[13px] text-text-secondary">{r.condition}</td>
                  <td className="py-2.5 px-3 text-[13px] text-text-muted">{r.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Test route */}
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Test Routing</h3>
        <div className="grid grid-cols-[2fr_1fr] gap-3 mb-3">
          <div>
            <label className="block text-[13px] text-text-muted mb-1">Query</label>
            <input className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface" placeholder="Enter a query to test routing..." value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <div>
            <label className="block text-[13px] text-text-muted mb-1">Default Model</label>
            <select className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface" value={defaultModel} onChange={(e) => setDefaultModel(e.target.value)}>
              {models.length === 0 && <option value="">No models configured</option>}
              {models.map((m) => (
                <option key={m.id} value={m.id}>{m.id} ({m.provider})</option>
              ))}
            </select>
          </div>
        </div>
        <Button onClick={handleTest} disabled={testing || !query}>
          {testing ? 'Testing...' : 'Test Route'}
        </Button>

        {result && (
          <div className="mt-md bg-bg-subtle rounded-md p-4">
            <div className="grid grid-cols-2 gap-md">
              <div>
                <div className="text-xs text-text-muted mb-1">Selected Model</div>
                <div className="text-lg font-semibold text-primary">{result.selected_model}</div>
              </div>
              <div>
                <div className="text-xs text-text-muted mb-1">Default Model</div>
                <div className="text-lg font-medium text-text-muted">{result.default_model}</div>
              </div>
            </div>
            {result.selected_model !== result.default_model && (
              <div className="mt-3 px-3 py-2 bg-success-light rounded text-[13px] text-success">
                A routing rule matched and overrode the default model.
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
