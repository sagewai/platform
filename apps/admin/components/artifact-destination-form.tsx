"use client";

import { useEffect, useMemo, useState } from "react";
import { adminApi } from "@/utils/api";
import type {
  ArtifactDestination,
  ArtifactDestinationType,
} from "@/utils/types";

const PLACEHOLDERS: Record<ArtifactDestinationType, string> = {
  github: "https://github.com/<org>/<repo>.git",
  s3: "<bucket>/<optional-prefix>",
  local: "/host/output",
};

const TARGET_LABELS: Record<ArtifactDestinationType, string> = {
  github: "Repo URL",
  s3: "Bucket / prefix",
  local: "Host-mounted path",
};

const ADVANCED_OPTIONS: Record<ArtifactDestinationType, { key: string; placeholder: string }[]> = {
  github: [
    { key: "branch", placeholder: "main" },
    { key: "commit_message", placeholder: "sagewai run <run_id>" },
  ],
  s3: [
    { key: "region", placeholder: "us-east-1" },
    { key: "storage_class", placeholder: "STANDARD" },
  ],
  local: [
    { key: "preserve_workspace", placeholder: "true | false" },
    { key: "mode", placeholder: "0644" },
  ],
};

interface Props {
  workflowName: string;
  effectiveSecretKeys?: string[];
}

export function ArtifactDestinationForm({
  workflowName,
  effectiveSecretKeys = [],
}: Props) {
  const [type, setType] = useState<ArtifactDestinationType>("github");
  const [target, setTarget] = useState("");
  const [envKeys, setEnvKeys] = useState<string[]>([]);
  const [options, setOptions] = useState<Record<string, string>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [hasOverride, setHasOverride] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!workflowName) return;
    let cancelled = false;
    setLoaded(false);
    adminApi.getWorkflowArtifactDestination(workflowName).then((dest) => {
      if (cancelled) return;
      if (dest) {
        setType(dest.type);
        setTarget(dest.target);
        setEnvKeys(dest.env_keys);
        setOptions(dest.options);
        setHasOverride(true);
      } else {
        setType("github");
        setTarget("");
        setEnvKeys([]);
        setOptions({});
        setHasOverride(false);
      }
      setLoaded(true);
    }).catch(() => {
      setLoaded(true);
    });
    return () => { cancelled = true; };
  }, [workflowName]);

  const optionRows = useMemo(() => ADVANCED_OPTIONS[type], [type]);

  function toggleEnvKey(key: string) {
    setEnvKeys((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  }

  async function onSave() {
    setBusy(true);
    setError(null);
    try {
      const dest: ArtifactDestination = {
        type,
        target: target.trim(),
        env_keys: envKeys,
        options: Object.fromEntries(
          Object.entries(options).filter(([, v]) => v.trim().length > 0),
        ),
      };
      await adminApi.putWorkflowArtifactDestination(workflowName, dest);
      setHasOverride(true);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function onClear() {
    setBusy(true);
    setError(null);
    try {
      await adminApi.deleteWorkflowArtifactDestination(workflowName);
      setHasOverride(false);
      setTarget("");
      setEnvKeys([]);
      setOptions({});
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  if (!workflowName) return null;
  if (!loaded) return <div className="text-xs text-neutral-500">Loading artifact destination…</div>;

  return (
    <section
      data-testid="artifact-destination-card"
      className="mt-md border rounded-md p-4"
    >
      <header className="flex items-baseline justify-between mb-2">
        <h2 className="text-lg font-semibold">Artifact destination</h2>
        <span className="text-xs text-neutral-500">
          {hasOverride ? "Source: workflow admin override" : "Source: code default or none"}
        </span>
      </header>
      <p className="text-xs text-neutral-500 mb-3">
        Where Mode 3+ runs of this workflow upload <code>/workspace</code> contents
        after completion. Credentials come from the workflow&apos;s resolved Sealed
        cascade — pick the env keys the upload subprocess is allowed to read.
      </p>

      <label className="block mb-2">
        <span className="text-xs">Type</span>
        <select
          aria-label="Artifact destination type"
          value={type}
          onChange={(e) => setType(e.target.value as ArtifactDestinationType)}
          className="block w-full rounded border p-1 text-sm mt-1"
        >
          <option value="github">GitHub</option>
          <option value="s3">S3</option>
          <option value="local">Local</option>
        </select>
      </label>

      <label className="block mb-2">
        <span className="text-xs">{TARGET_LABELS[type]}</span>
        <input
          aria-label="Artifact destination target"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder={PLACEHOLDERS[type]}
          className="block w-full rounded border p-1 text-sm mt-1"
        />
      </label>

      <fieldset className="mb-2">
        <legend className="text-xs">
          Env keys (Sealed-injected secrets the upload subprocess reads)
        </legend>
        {effectiveSecretKeys.length === 0 ? (
          <p className="text-xs text-neutral-400 mt-1">
            No effective_secret_keys yet — load the cascade preview above first.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2 mt-1">
            {effectiveSecretKeys.map((key) => (
              <label key={key} className="text-xs flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={envKeys.includes(key)}
                  onChange={() => toggleEnvKey(key)}
                />
                <span className="font-mono">{key}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>

      <button
        type="button"
        onClick={() => setShowAdvanced((v) => !v)}
        className="text-xs text-blue-600 bg-transparent border-0 cursor-pointer mb-1"
      >
        {showAdvanced ? "▾ Advanced options" : "▸ Advanced options"}
      </button>
      {showAdvanced && (
        <div className="mb-2 space-y-1">
          {optionRows.map(({ key, placeholder }) => (
            <label key={key} className="block text-xs">
              <span className="text-neutral-500 font-mono">{key}</span>
              <input
                value={options[key] ?? ""}
                onChange={(e) =>
                  setOptions({ ...options, [key]: e.target.value })
                }
                placeholder={placeholder}
                className="block w-full rounded border p-1 text-sm mt-0.5"
              />
            </label>
          ))}
        </div>
      )}

      {error && (
        <div className="text-xs text-rose-600 mb-2" role="alert">
          {error}
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={onSave}
          disabled={busy || !target.trim()}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50 cursor-pointer"
        >
          {busy ? "Saving…" : "Save"}
        </button>
        {hasOverride && (
          <button
            type="button"
            onClick={onClear}
            disabled={busy}
            className="rounded border border-rose-600 text-rose-600 px-3 py-1.5 text-sm disabled:opacity-50 cursor-pointer"
          >
            Clear admin override
          </button>
        )}
      </div>
    </section>
  );
}
