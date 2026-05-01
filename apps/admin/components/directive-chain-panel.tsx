"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { DirectiveEvaluation } from "@/utils/types";

export function DirectiveChainPanel({ runId }: { runId: string }) {
  const [events, setEvents] = useState<DirectiveEvaluation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi
      .getRunDirectiveSummary(runId)
      .then((r) => setEvents(r.events))
      .catch((e: unknown) => setError(String(e)));
  }, [runId]);

  if (error) {
    return (
      <p className="text-xs text-red-600 m-0">
        Could not load directive timeline: {error}
      </p>
    );
  }

  if (events.length === 0) {
    return (
      <p className="text-sm text-text-muted m-0">
        No directive events recorded for this run.
      </p>
    );
  }

  return (
    <ul className="text-sm space-y-1 m-0 list-none p-0">
      {events.map((e) => (
        <li
          key={e.id}
          className="font-[family-name:var(--font-mono)] text-xs"
        >
          {new Date(e.created_at).toLocaleString()} ·{" "}
          <span className="font-semibold">{e.event_type}</span>
          {e.policy_id ? ` · policy ${e.policy_id}` : ""}
          {e.signal_kind ? ` · ${e.signal_kind}` : ""}
          {e.severity ? ` · ${e.severity}` : ""}
        </li>
      ))}
    </ul>
  );
}
