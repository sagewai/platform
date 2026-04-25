"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { EffectiveProfile } from "@/utils/types";

interface Props {
  project?: string;
  workflow?: string;
  user_profile_ref?: string;
  user_overrides?: Record<string, string>;
}

export function SealedCascadePreview(props: Props) {
  const [eff, setEff] = useState<EffectiveProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    adminApi
      .getSealedPreview(props)
      .then(setEff)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.project, props.workflow, props.user_profile_ref, JSON.stringify(props.user_overrides)]);

  if (error) return <div className="text-rose-600 text-sm">Preview error: {error}</div>;
  if (!eff) return <div className="text-neutral-500 text-sm">Loading preview…</div>;

  const allKeys = Array.from(new Set([
    ...Object.keys(eff.env),
    ...eff.secret_keys,
  ])).sort();

  if (allKeys.length === 0) {
    return (
      <div className="text-sm text-neutral-500 italic">
        No keys resolve under the current cascade.
      </div>
    );
  }

  return (
    <div className="text-sm">
      <h3 className="font-semibold mb-2">Effective profile (preview)</h3>
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b text-left">
            <th className="py-1">Key</th>
            <th className="py-1">Value</th>
            <th className="py-1">Origin</th>
            <th className="py-1">Type</th>
          </tr>
        </thead>
        <tbody>
          {allKeys.map((k) => {
            const isSecret = eff.secret_keys.includes(k);
            return (
              <tr key={k} className="border-b">
                <td className="py-1 font-mono">{k}</td>
                <td className="py-1">{isSecret ? "••••••••" : eff.env[k]}</td>
                <td className="py-1">
                  <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs">
                    {eff.cascade_origins[k] ?? "—"}
                  </span>
                </td>
                <td className="py-1">{isSecret ? "secret" : "env"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
