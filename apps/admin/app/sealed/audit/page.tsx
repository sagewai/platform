"use client";

import { useState } from "react";
import { adminApi } from "@/utils/api";
import type { SealedAuditEvent } from "@/utils/types";

export default function SealedAuditPage() {
  const [events, setEvents] = useState<SealedAuditEvent[]>([]);
  const [profileId, setProfileId] = useState("");
  const [eventType, setEventType] = useState("");
  const [actorId, setActorId] = useState("");
  const [busy, setBusy] = useState(false);

  async function search() {
    setBusy(true);
    try {
      const result = await adminApi.getSealedAudit({
        profile_id: profileId || undefined,
        event_type: eventType || undefined,
        actor_id: actorId || undefined,
        limit: 200,
      });
      setEvents(result);
    } finally {
      setBusy(false);
    }
  }

  function exportCsv() {
    if (events.length === 0) return;
    const header = "id,event_type,actor_type,actor_id,profile_id,secret_key,run_id,project_id,created_at,details\n";
    const rows = events.map((e) =>
      [e.id, e.event_type, e.actor_type, e.actor_id ?? "", e.profile_id ?? "",
       e.secret_key ?? "", e.run_id ?? "", e.project_id ?? "", e.created_at,
       JSON.stringify(e.details)]
        .map((v) => `"${String(v).replace(/"/g, '""')}"`)
        .join(",")
    ).join("\n");
    const blob = new Blob([header + rows], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sealed-audit-${new Date().toISOString()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold mb-md">Sealed Audit Log</h1>

      <div className="flex flex-wrap gap-2 mb-md">
        <input
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          placeholder="profile_id"
          className="rounded border p-1 text-sm"
        />
        <input
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
          placeholder="event_type"
          className="rounded border p-1 text-sm"
        />
        <input
          value={actorId}
          onChange={(e) => setActorId(e.target.value)}
          placeholder="actor_id"
          className="rounded border p-1 text-sm"
        />
        <button
          type="button"
          onClick={search}
          disabled={busy}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50 cursor-pointer"
        >
          {busy ? "Searching…" : "Search"}
        </button>
        <button
          type="button"
          onClick={exportCsv}
          disabled={events.length === 0}
          className="rounded border px-3 py-1.5 text-sm disabled:opacity-50 cursor-pointer"
        >
          Export CSV
        </button>
      </div>

      <table className="w-full text-sm border-collapse">
        <thead className="text-left border-b">
          <tr>
            <th className="py-1">ID</th>
            <th className="py-1">Event</th>
            <th className="py-1">Actor</th>
            <th className="py-1">Profile</th>
            <th className="py-1">Secret</th>
            <th className="py-1">Run</th>
            <th className="py-1">When</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="border-b">
              <td className="font-mono text-xs py-1">{e.id}</td>
              <td className="font-mono text-xs py-1">{e.event_type}</td>
              <td className="py-1">{e.actor_id ?? "—"}</td>
              <td className="py-1">{e.profile_id ?? "—"}</td>
              <td className="py-1">{e.secret_key ?? "—"}</td>
              <td className="font-mono text-xs py-1">{e.run_id ?? "—"}</td>
              <td className="text-xs py-1">{new Date(e.created_at).toLocaleString()}</td>
            </tr>
          ))}
          {events.length === 0 && (
            <tr>
              <td colSpan={7} className="text-center text-neutral-500 py-md">
                No events. Search above.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
