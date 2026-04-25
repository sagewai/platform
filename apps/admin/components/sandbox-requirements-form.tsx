"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type {
  SandboxRequirementsPayload,
  SandboxRequirementsResponse,
} from "@/utils/types";

interface Props {
  scope: "project" | "agent";
  scopeId: string;
  initialValues: SandboxRequirementsResponse | null;
  onSaved: (response: SandboxRequirementsResponse) => void;
  onCleared: () => void;
  showCascadeOriginBadges?: boolean;
}

const MODE_OPTIONS: Array<SandboxRequirementsPayload["sandbox_mode"]> = [
  "none", "per_tool", "per_run", "per_worker",
];

const NETWORK_POLICY_OPTIONS: Array<SandboxRequirementsPayload["network_policy"]> = [
  "none", "egress_allowlist", "full",
];

export function SandboxRequirementsForm({
  scope,
  scopeId,
  initialValues,
  onSaved,
  onCleared,
}: Props) {
  const [mode, setMode] = useState<SandboxRequirementsPayload["sandbox_mode"]>(
    initialValues?.sandbox_mode ?? "per_run"
  );
  const [image, setImage] = useState(initialValues?.image ?? "");
  const [networkPolicy, setNetworkPolicy] = useState<
    SandboxRequirementsPayload["network_policy"]
  >(initialValues?.network_policy ?? "none");
  const [variantBadge, setVariantBadge] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refreshVariantBadge(currentImage: string) {
    if (!currentImage) {
      setVariantBadge(null);
      return;
    }
    try {
      const preview = await adminApi.getSandboxResolutionPreview({
        ...(scope === "project" ? { project: scopeId } : { agent: scopeId }),
        draft: { sandbox_mode: mode, image: currentImage, network_policy: networkPolicy, required_secret_scopes: [] },
      });
      setVariantBadge(preview.variant ?? "BYO");
    } catch {
      setVariantBadge(null);
    }
  }

  useEffect(() => {
    refreshVariantBadge(image);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      const payload: SandboxRequirementsPayload = {
        sandbox_mode: mode,
        image,
        network_policy: networkPolicy,
        required_secret_scopes: [],
      };
      const response =
        scope === "project"
          ? await adminApi.putProjectSandboxDefaults(scopeId, payload)
          : await adminApi.putAgentSandboxRequirements(scopeId, payload);
      onSaved(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleClear() {
    setBusy(true);
    setError(null);
    try {
      if (scope === "project") {
        await adminApi.deleteProjectSandboxDefaults(scopeId);
      } else {
        await adminApi.deleteAgentSandboxRequirements(scopeId);
      }
      onCleared();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const variantBadgeColor =
    variantBadge === "BYO" ? "bg-neutral-100 text-neutral-600"
    : variantBadge ? "bg-emerald-500/10 text-emerald-700"
    : "bg-amber-500/10 text-amber-700";

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs font-medium text-neutral-700">Sandbox mode</label>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as typeof mode)}
          disabled={busy}
          className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        >
          {MODE_OPTIONS.map((m) => (<option key={m} value={m}>{m}</option>))}
        </select>
      </div>

      <div>
        <label className="block text-xs font-medium text-neutral-700">Image</label>
        <input
          type="text"
          value={image}
          onChange={(e) => setImage(e.target.value)}
          onBlur={(e) => refreshVariantBadge(e.target.value)}
          disabled={busy}
          placeholder="ghcr.io/sagewai/sandbox-general:0.1.5"
          className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-sm font-mono"
        />
        {variantBadge && (
          <span className={`mt-1 inline-flex items-center rounded border border-current/20 px-1.5 py-0.5 text-xs font-medium ${variantBadgeColor}`}>
            [{variantBadge}]
          </span>
        )}
      </div>

      <div>
        <label className="block text-xs font-medium text-neutral-700">Network policy</label>
        <select
          value={networkPolicy}
          onChange={(e) => setNetworkPolicy(e.target.value as typeof networkPolicy)}
          disabled={busy}
          className="mt-1 w-full rounded border border-neutral-300 px-2 py-1 text-sm"
        >
          {NETWORK_POLICY_OPTIONS.map((p) => (<option key={p} value={p}>{p}</option>))}
        </select>
      </div>

      {error && <div className="rounded bg-rose-500/10 px-2 py-1 text-xs text-rose-700">{error}</div>}

      <div className="flex gap-2">
        <button onClick={handleSave} disabled={busy || !image} className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">
          {initialValues ? "Update" : "Save"}
        </button>
        {initialValues && (
          <button
            onClick={() => { if (confirm("Clear sandbox config? In-flight runs are not affected.")) handleClear(); }}
            disabled={busy}
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700"
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
