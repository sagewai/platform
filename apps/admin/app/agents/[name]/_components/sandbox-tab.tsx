"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type {
  AgentDetail,
  SandboxRequirementsResponse,
  SandboxResolutionPreview,
} from "@/utils/types";
import { SandboxRequirementsForm } from "@/components/sandbox-requirements-form";

interface Props {
  agent: AgentDetail;
}

export function SandboxTab({ agent }: Props) {
  const [override, setOverride] = useState<SandboxRequirementsResponse | null>(null);
  const [preview, setPreview] = useState<SandboxResolutionPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const [o, p] = await Promise.all([
        adminApi.getAgentSandboxRequirements(agent.name),
        adminApi.getSandboxResolutionPreview({ agent: agent.name }),
      ]);
      setOverride(o);
      setPreview(p);
      setShowForm(o !== null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.name]);

  if (loading) return <div className="text-sm text-neutral-500">Loading sandbox config…</div>;

  return (
    <div className="space-y-4">
      <div className="rounded border border-neutral-200 bg-neutral-50 p-3">
        <h4 className="text-xs font-semibold text-neutral-700">Currently resolved</h4>
        {preview && (
          <ul className="mt-2 space-y-1 text-xs font-mono text-neutral-700">
            <li>
              mode: {preview.sandbox_mode.value}
              <span className="ml-2 text-neutral-500">(from {preview.sandbox_mode.origin})</span>
            </li>
            <li>
              image: {preview.image.value}
              {preview.variant && (
                <span className="ml-2 inline-flex rounded border border-emerald-500/20 bg-emerald-500/10 px-1 py-0.5 text-emerald-700">
                  {preview.variant}
                </span>
              )}
              <span className="ml-2 text-neutral-500">(from {preview.image.origin})</span>
            </li>
            <li>
              network: {preview.network_policy.value}
              <span className="ml-2 text-neutral-500">(from {preview.network_policy.origin})</span>
            </li>
          </ul>
        )}
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={showForm}
          onChange={(e) => setShowForm(e.target.checked)}
          disabled={loading}
        />
        Override at agent level
      </label>

      {showForm && (
        <SandboxRequirementsForm
          scope="agent"
          scopeId={agent.name}
          initialValues={override ?? preview?.resolved ?? null}
          onSaved={(r) => {
            setOverride(r);
            refresh();
          }}
          onCleared={() => {
            setOverride(null);
            setShowForm(false);
            refresh();
          }}
          showCascadeOriginBadges
        />
      )}
    </div>
  );
}
