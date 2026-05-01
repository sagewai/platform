"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { DirectivePolicy, DirectivesConfig } from "@/utils/types";

export default function DirectivesPage() {
  const [config, setConfig] = useState<DirectivesConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    adminApi
      .getDirectivePolicies()
      .then(setConfig)
      .catch((e: unknown) => setError(String(e)));
  }, []);

  if (error)
    return (
      <main className="p-6">
        <div className="text-red-600">Failed to load: {error}</div>
      </main>
    );
  if (!config) return <main className="p-6">Loading…</main>;

  const togglePolicyEnabled = async (idx: number) => {
    setBusy(true);
    try {
      const next = config.system_policies.map((p, i) =>
        i === idx ? { ...p, enabled: !p.enabled } : p,
      );
      const updated: DirectivesConfig = { ...config, system_policies: next };
      await adminApi.putDirectivePolicies(updated);
      setConfig(updated);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Directive policies</h1>
      <p className="text-sm text-muted-foreground">
        Reactive directives observe runs and react to signals (cost overrun,
        capability gap, rotation drift). Defaults are alert-only — promote,
        abort, and restart actions opt-in.
      </p>

      <section>
        <h2 className="text-lg font-medium mb-2">System policies</h2>
        <PolicyTable
          policies={config.system_policies}
          onToggle={togglePolicyEnabled}
          disabled={busy}
        />
      </section>

      {Object.keys(config.project_policies).length > 0 && (
        <section>
          <h2 className="text-lg font-medium mb-2">Project policies</h2>
          {Object.entries(config.project_policies).map(([proj, pols]) => (
            <div key={proj} className="mb-4">
              <h3 className="text-sm font-mono text-muted-foreground">{proj}</h3>
              <PolicyTable policies={pols} disabled />
            </div>
          ))}
        </section>
      )}

      {Object.keys(config.workflow_policies).length > 0 && (
        <section>
          <h2 className="text-lg font-medium mb-2">Workflow policies</h2>
          {Object.entries(config.workflow_policies).map(([wf, pols]) => (
            <div key={wf} className="mb-4">
              <h3 className="text-sm font-mono text-muted-foreground">{wf}</h3>
              <PolicyTable policies={pols} disabled />
            </div>
          ))}
        </section>
      )}
    </main>
  );
}

function PolicyTable(props: {
  policies: DirectivePolicy[];
  onToggle?: (idx: number) => void;
  disabled?: boolean;
}) {
  return (
    <table className="min-w-full text-sm border">
      <thead>
        <tr className="border-b">
          <th className="text-left p-2">ID</th>
          <th className="text-left p-2">Name</th>
          <th className="text-left p-2">Signal</th>
          <th className="text-left p-2">Action</th>
          <th className="text-left p-2">Approval</th>
          <th className="text-left p-2">Enabled</th>
        </tr>
      </thead>
      <tbody>
        {props.policies.map((p, i) => (
          <tr key={p.id} className="border-b">
            <td className="p-2 font-mono">{p.id}</td>
            <td className="p-2">{p.name}</td>
            <td className="p-2">{p.condition.signal_kind}</td>
            <td className="p-2">{p.action.kind}</td>
            <td className="p-2">{p.requires_approval ? "yes" : "no"}</td>
            <td className="p-2">
              <label>
                <input
                  type="checkbox"
                  checked={p.enabled}
                  disabled={props.disabled || !props.onToggle}
                  onChange={() => props.onToggle?.(i)}
                />
              </label>
            </td>
          </tr>
        ))}
        {props.policies.length === 0 && (
          <tr>
            <td colSpan={6} className="p-2 text-muted-foreground">
              No policies configured.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
