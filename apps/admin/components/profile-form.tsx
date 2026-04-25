"use client";

import { useState } from "react";
import { adminApi } from "@/utils/api";
import type { Profile, ProfileWritePayload } from "@/utils/types";

interface Props {
  initial?: Profile;          // null/undefined for create
  profileId?: string;          // for update; required if initial is null and you want to set the id
  onSaved?: (saved: Profile) => void;
}

export function ProfileForm({ initial, profileId, onSaved }: Props) {
  const [id, setId] = useState(initial?.id ?? profileId ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [owner, setOwner] = useState(initial?.owner ?? "");
  const [tagsCsv, setTagsCsv] = useState((initial?.tags ?? []).join(", "));
  const [allowedCsv, setAllowedCsv] = useState((initial?.allowed_workflows ?? []).join(", "));
  const [envRows, setEnvRows] = useState<{ key: string; value: string }[]>(
    Object.entries(initial?.env ?? {}).map(([k, v]) => ({ key: k, value: v }))
  );
  const [secretRows, setSecretRows] = useState<{ key: string; value: string }[]>(
    Object.entries(initial?.secrets ?? {}).map(([k, v]) => ({ key: k, value: v }))
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSave() {
    setBusy(true);
    setError(null);
    try {
      const payload: ProfileWritePayload = {
        id: id || undefined,
        name,
        description,
        owner: owner || undefined,
        tags: tagsCsv.split(",").map((s) => s.trim()).filter(Boolean),
        allowed_workflows: allowedCsv.split(",").map((s) => s.trim()).filter(Boolean),
        env: Object.fromEntries(envRows.filter((r) => r.key).map((r) => [r.key, r.value])),
        secrets: Object.fromEntries(secretRows.filter((r) => r.key).map((r) => [r.key, r.value])),
      };
      const saved = initial?.id
        ? await adminApi.updateSealedProfile(initial.id, payload)
        : await adminApi.createProfile(payload);
      onSaved?.(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {!initial && (
        <label className="block">
          <span className="text-xs font-medium">Profile ID</span>
          <input
            value={id}
            onChange={(e) => setId(e.target.value)}
            className="block w-full rounded border p-1 text-sm"
            placeholder="e.g. acme-prod"
          />
        </label>
      )}
      <label className="block">
        <span className="text-xs font-medium">Name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="block w-full rounded border p-1 text-sm"
          required
        />
      </label>
      <label className="block">
        <span className="text-xs font-medium">Description</span>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="block w-full rounded border p-1 text-sm"
          rows={2}
        />
      </label>
      <label className="block">
        <span className="text-xs font-medium">Owner</span>
        <input
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          className="block w-full rounded border p-1 text-sm"
        />
      </label>
      <label className="block">
        <span className="text-xs font-medium">Tags (comma-separated)</span>
        <input
          value={tagsCsv}
          onChange={(e) => setTagsCsv(e.target.value)}
          className="block w-full rounded border p-1 text-sm"
        />
      </label>
      <label className="block">
        <span className="text-xs font-medium">Allowed workflows (comma-separated)</span>
        <input
          value={allowedCsv}
          onChange={(e) => setAllowedCsv(e.target.value)}
          className="block w-full rounded border p-1 text-sm"
          placeholder="empty = all workflows allowed"
        />
      </label>

      <fieldset className="space-y-2">
        <legend className="text-xs font-medium">Environment variables</legend>
        {envRows.map((row, i) => (
          <div key={i} className="flex gap-2">
            <input
              value={row.key}
              onChange={(e) => {
                const next = [...envRows];
                next[i].key = e.target.value;
                setEnvRows(next);
              }}
              placeholder="KEY"
              className="rounded border p-1 text-sm flex-1"
            />
            <input
              value={row.value}
              onChange={(e) => {
                const next = [...envRows];
                next[i].value = e.target.value;
                setEnvRows(next);
              }}
              placeholder="value"
              className="rounded border p-1 text-sm flex-2"
            />
            <button
              onClick={() => setEnvRows(envRows.filter((_, j) => j !== i))}
              className="text-xs text-rose-600"
            >
              Remove
            </button>
          </div>
        ))}
        <button
          onClick={() => setEnvRows([...envRows, { key: "", value: "" }])}
          className="text-xs text-blue-600"
        >
          + Add env var
        </button>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs font-medium">Secrets</legend>
        {secretRows.map((row, i) => (
          <div key={i} className="flex gap-2">
            <input
              value={row.key}
              onChange={(e) => {
                const next = [...secretRows];
                next[i].key = e.target.value;
                setSecretRows(next);
              }}
              placeholder="SECRET_KEY"
              className="rounded border p-1 text-sm flex-1"
            />
            <input
              type="password"
              value={row.value}
              onChange={(e) => {
                const next = [...secretRows];
                next[i].value = e.target.value;
                setSecretRows(next);
              }}
              placeholder="value (encrypted at rest)"
              className="rounded border p-1 text-sm flex-2"
            />
            <button
              onClick={() => setSecretRows(secretRows.filter((_, j) => j !== i))}
              className="text-xs text-rose-600"
            >
              Remove
            </button>
          </div>
        ))}
        <button
          onClick={() => setSecretRows([...secretRows, { key: "", value: "" }])}
          className="text-xs text-blue-600"
        >
          + Add secret
        </button>
      </fieldset>

      {error && <div className="text-rose-600 text-sm">{error}</div>}

      <button
        onClick={onSave}
        disabled={busy || !name}
        className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
      >
        {busy ? "Saving…" : initial ? "Update" : "Create"}
      </button>
    </div>
  );
}
