"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { ProfileMetadata, SealedWorkflowConfig } from "@/utils/types";
import { SealedCascadePreview } from "@/components/sealed-cascade-preview";

export default function WorkflowSealedPage() {
  const [workflowName, setWorkflowName] = useState("");
  const [cfg, setCfg] = useState<SealedWorkflowConfig | null>(null);
  const [profiles, setProfiles] = useState<ProfileMetadata[]>([]);
  const [overrides, setOverrides] = useState<{ key: string; value: string }[]>([]);
  const [profileRef, setProfileRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    adminApi.listProfiles().then(setProfiles).catch(() => {});
  }, []);

  async function loadWorkflow() {
    if (!workflowName) return;
    const c = await adminApi.getSealedWorkflow(workflowName);
    if (c) {
      setCfg(c);
      setProfileRef(c.profile_ref ?? "");
      setOverrides(Object.entries(c.overrides).map(([k, v]) => ({ key: k, value: v })));
    } else {
      setCfg({ profile_ref: null, overrides: {} });
      setProfileRef("");
      setOverrides([]);
    }
    setLoaded(true);
  }

  async function onSave() {
    setBusy(true);
    try {
      const next: SealedWorkflowConfig = {
        profile_ref: profileRef || null,
        overrides: Object.fromEntries(overrides.filter((r) => r.key).map((r) => [r.key, r.value])),
      };
      await adminApi.putSealedWorkflow(workflowName, next);
      setCfg(next);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold mb-md">Workflow Security Profiles</h1>
      <p className="text-xs text-neutral-500 mb-4">
        Per-workflow sealed cascade overlay. Layered on top of the system default
        and overridden by user-level kwargs at enqueue time.
      </p>

      <div className="flex gap-2 mb-md">
        <input
          value={workflowName}
          onChange={(e) => setWorkflowName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") loadWorkflow(); }}
          placeholder="workflow name"
          className="rounded border p-1 text-sm flex-1"
        />
        <button
          type="button"
          onClick={loadWorkflow}
          disabled={!workflowName}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50 cursor-pointer"
        >
          Load
        </button>
      </div>

      {loaded && cfg && (
        <>
          <label className="block mb-2">
            <span className="text-xs">Profile</span>
            <select
              value={profileRef}
              onChange={(e) => setProfileRef(e.target.value)}
              className="block w-full rounded border p-1 text-sm mt-1"
            >
              <option value="">— inherit from system —</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
              ))}
            </select>
          </label>

          <fieldset className="mb-2">
            <legend className="text-xs">Inline overrides</legend>
            {overrides.map((row, i) => (
              <div key={i} className="flex gap-2 mt-1">
                <input
                  value={row.key}
                  onChange={(e) => {
                    const next = [...overrides]; next[i] = { ...next[i], key: e.target.value }; setOverrides(next);
                  }}
                  placeholder="KEY"
                  className="rounded border p-1 text-sm flex-1"
                />
                <input
                  value={row.value}
                  onChange={(e) => {
                    const next = [...overrides]; next[i] = { ...next[i], value: e.target.value }; setOverrides(next);
                  }}
                  placeholder="value (empty = remove key)"
                  className="rounded border p-1 text-sm flex-1"
                />
                <button
                  type="button"
                  onClick={() => setOverrides(overrides.filter((_, j) => j !== i))}
                  className="text-xs text-rose-600 bg-transparent border-0 cursor-pointer"
                >
                  Remove
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setOverrides([...overrides, { key: "", value: "" }])}
              className="mt-1 text-xs text-blue-600 bg-transparent border-0 cursor-pointer"
            >
              + Add override
            </button>
          </fieldset>

          <button
            type="button"
            onClick={onSave}
            disabled={busy}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50 cursor-pointer"
          >
            {busy ? "Saving…" : "Save workflow config"}
          </button>

          <div className="mt-md">
            <SealedCascadePreview workflow={workflowName} />
          </div>
        </>
      )}
    </div>
  );
}
