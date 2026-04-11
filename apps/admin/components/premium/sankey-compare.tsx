'use client';

import { useState } from 'react';
import { SankeyDiagram } from './sankey-diagram';

interface AgentTokenData {
  name: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

interface RunOption {
  run_id: string;
  workflow_name: string;
  agents: AgentTokenData[];
}

interface SankeyCompareProps {
  runs: RunOption[];
}

export function SankeyCompare({ runs }: SankeyCompareProps) {
  const [runA, setRunA] = useState<string>(runs[0]?.run_id ?? '');
  const [runB, setRunB] = useState<string>(runs[1]?.run_id ?? '');

  const dataA = runs.find((r) => r.run_id === runA);
  const dataB = runs.find((r) => r.run_id === runB);

  // Compute cost differences
  const diffs = (() => {
    if (!dataA || !dataB) return [];
    const mapA = new Map(dataA.agents.map((a) => [a.name, a.total_tokens]));
    const mapB = new Map(dataB.agents.map((a) => [a.name, a.total_tokens]));
    const allNames = new Set([...mapA.keys(), ...mapB.keys()]);
    return Array.from(allNames).map((name) => {
      const tokA = mapA.get(name) ?? 0;
      const tokB = mapB.get(name) ?? 0;
      const pctChange = tokA > 0 ? ((tokB - tokA) / tokA) * 100 : tokB > 0 ? 100 : 0;
      return { name, tokA, tokB, pctChange };
    });
  })();

  return (
    <div>
      {/* Run selectors */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <label className="block text-xs text-text-muted mb-1">Run A</label>
          <select
            value={runA}
            onChange={(e) => setRunA(e.target.value)}
            className="w-full px-2 py-1.5 text-xs border border-border rounded bg-bg-surface text-text-primary"
          >
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.workflow_name} ({r.run_id.slice(0, 8)}...)
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-text-muted mb-1">Run B</label>
          <select
            value={runB}
            onChange={(e) => setRunB(e.target.value)}
            className="w-full px-2 py-1.5 text-xs border border-border rounded bg-bg-surface text-text-primary"
          >
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.workflow_name} ({r.run_id.slice(0, 8)}...)
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Side-by-side Sankey */}
      <div className="grid grid-cols-2 gap-4">
        <div className="border border-border rounded-lg p-3 overflow-auto">
          <div className="text-xs text-text-muted font-semibold mb-2">Run A</div>
          {dataA ? (
            <SankeyDiagram agents={dataA.agents} />
          ) : (
            <div className="text-xs text-text-muted">Select a run</div>
          )}
        </div>
        <div className="border border-border rounded-lg p-3 overflow-auto">
          <div className="text-xs text-text-muted font-semibold mb-2">Run B</div>
          {dataB ? (
            <SankeyDiagram agents={dataB.agents} />
          ) : (
            <div className="text-xs text-text-muted">Select a run</div>
          )}
        </div>
      </div>

      {/* Difference table */}
      {diffs.length > 0 && (
        <div className="mt-4 border border-border rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-bg-subtle text-text-muted">
                <th className="text-left px-3 py-2 font-medium">Agent</th>
                <th className="text-right px-3 py-2 font-medium">Run A Tokens</th>
                <th className="text-right px-3 py-2 font-medium">Run B Tokens</th>
                <th className="text-right px-3 py-2 font-medium">Change</th>
              </tr>
            </thead>
            <tbody>
              {diffs.map((d) => (
                <tr key={d.name} className="border-t border-border">
                  <td className="px-3 py-2 font-medium text-text-primary">{d.name}</td>
                  <td className="px-3 py-2 text-right text-text-muted">{d.tokA.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right text-text-muted">{d.tokB.toLocaleString()}</td>
                  <td
                    className={`px-3 py-2 text-right font-medium ${
                      Math.abs(d.pctChange) > 20
                        ? d.pctChange > 0
                          ? 'text-error'
                          : 'text-success'
                        : 'text-text-muted'
                    }`}
                  >
                    {d.pctChange > 0 ? '+' : ''}
                    {d.pctChange.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
