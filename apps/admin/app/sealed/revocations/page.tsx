"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { Revocation } from "@/utils/types";

export default function RevocationsPage() {
  const [items, setItems] = useState<Revocation[]>([]);
  const [includeLifted, setIncludeLifted] = useState(false);
  const [filterProfile, setFilterProfile] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const data = await adminApi.listRevocations({
        profile_id: filterProfile || undefined,
        include_lifted: includeLifted,
      });
      setItems(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [includeLifted]);

  async function onLift(id: number) {
    if (!confirm(`Lift revocation ${id}?`)) return;
    setBusy(true);
    try {
      await adminApi.liftRevocation(id);
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div>Loading…</div>;

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold mb-md">Sealed Revocations</h1>

      <div className="flex gap-2 mb-md flex-wrap">
        <input
          value={filterProfile}
          onChange={(e) => setFilterProfile(e.target.value)}
          placeholder="filter by profile_id"
          className="rounded border p-1 text-sm"
        />
        <button
          onClick={load}
          className="rounded border px-3 py-1.5 text-sm"
        >
          Filter
        </button>
        <label className="text-sm flex items-center gap-1">
          <input
            type="checkbox"
            checked={includeLifted}
            onChange={(e) => setIncludeLifted(e.target.checked)}
          />{" "}
          Include lifted
        </label>
      </div>

      <table className="w-full text-sm border-collapse">
        <thead className="text-left border-b">
          <tr>
            <th>ID</th>
            <th>Profile</th>
            <th>Secret</th>
            <th>Revoked at</th>
            <th>By</th>
            <th>Reason</th>
            <th>Hard?</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.id} className="border-b">
              <td className="font-mono text-xs">{r.id}</td>
              <td className="font-mono text-xs">{r.profile_id}</td>
              <td className="font-mono text-xs">{r.secret_key}</td>
              <td className="text-xs">{new Date(r.revoked_at).toLocaleString()}</td>
              <td>{r.revoked_by ?? "—"}</td>
              <td>{r.reason}</td>
              <td>{r.hard ? "yes" : "—"}</td>
              <td>
                {r.lifted_at ? (
                  <span className="text-xs">lifted {new Date(r.lifted_at).toLocaleString()}</span>
                ) : (
                  <span className="text-rose-600 text-xs">active</span>
                )}
              </td>
              <td>
                {!r.lifted_at && (
                  <button
                    onClick={() => onLift(r.id)}
                    disabled={busy}
                    className="text-xs text-blue-600 hover:underline disabled:opacity-50"
                  >
                    Lift
                  </button>
                )}
              </td>
            </tr>
          ))}
          {items.length === 0 && (
            <tr>
              <td colSpan={9} className="text-center text-neutral-500 py-md">
                No revocations active. Quiet is good.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
