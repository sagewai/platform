"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { DirectiveEvaluation } from "@/utils/types";

export default function DirectiveEvaluationsPage() {
  const [events, setEvents] = useState<DirectiveEvaluation[]>([]);
  const [filter, setFilter] = useState<{
    run_id?: string;
    policy_id?: string;
    event_type?: string;
  }>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi
      .listDirectiveEvaluations({ ...filter, limit: 200 })
      .then((r) => setEvents(r.events))
      .catch((e: unknown) => setError(String(e)));
  }, [filter]);

  return (
    <main className="p-6 space-y-4">
      <h1 className="text-2xl font-semibold">Directive evaluations</h1>
      <div className="flex gap-2 text-sm">
        <input
          placeholder="run_id"
          className="border rounded px-2 py-1"
          onBlur={(e) =>
            setFilter((f) => ({ ...f, run_id: e.target.value || undefined }))
          }
        />
        <input
          placeholder="policy_id"
          className="border rounded px-2 py-1"
          onBlur={(e) =>
            setFilter((f) => ({ ...f, policy_id: e.target.value || undefined }))
          }
        />
        <input
          placeholder="event_type"
          className="border rounded px-2 py-1"
          onBlur={(e) =>
            setFilter((f) => ({
              ...f,
              event_type: e.target.value || undefined,
            }))
          }
        />
      </div>

      {error && <div className="text-red-600 text-sm">Error: {error}</div>}

      <table className="min-w-full text-sm border">
        <thead>
          <tr className="border-b">
            <th className="text-left p-2">When</th>
            <th className="text-left p-2">Event</th>
            <th className="text-left p-2">Run</th>
            <th className="text-left p-2">Policy</th>
            <th className="text-left p-2">Signal</th>
            <th className="text-left p-2">Severity</th>
          </tr>
        </thead>
        <tbody>
          {events.length === 0 && (
            <tr>
              <td colSpan={6} className="p-2 text-muted-foreground">
                No evaluations recorded.
              </td>
            </tr>
          )}
          {events.map((e) => (
            <tr key={e.id} className="border-b">
              <td className="p-2">{new Date(e.created_at).toLocaleString()}</td>
              <td className="p-2 font-mono">{e.event_type}</td>
              <td className="p-2 font-mono">{e.run_id}</td>
              <td className="p-2">{e.policy_id ?? "—"}</td>
              <td className="p-2">{e.signal_kind ?? "—"}</td>
              <td className="p-2">{e.severity ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
