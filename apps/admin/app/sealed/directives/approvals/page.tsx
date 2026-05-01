"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";
import type { PendingApproval } from "@/utils/types";

export default function DirectiveApprovalsPage() {
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = () =>
    adminApi
      .listDirectiveApprovals()
      .then((r) => setPending(r.pending))
      .catch((e: unknown) => setError(String(e)));

  useEffect(() => {
    refresh();
  }, []);

  const decide = async (
    decisionId: string,
    action: "approve" | "deny",
  ): Promise<void> => {
    setBusy(decisionId);
    try {
      if (action === "approve") {
        await adminApi.approveDirective(decisionId, "default-admin", "");
      } else {
        await adminApi.denyDirective(decisionId, "default-admin", "");
      }
      await refresh();
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="p-6 space-y-4">
      <h1 className="text-2xl font-semibold">Pending directive approvals</h1>
      {error && <div className="text-red-600 text-sm">Error: {error}</div>}
      {pending.length === 0 && (
        <p className="text-muted-foreground">
          No pending approvals. Quiet is good.
        </p>
      )}
      <ul className="space-y-3">
        {pending.map((a) => {
          const action = a.proposed_action as { kind?: string };
          return (
            <li key={a.decision_id} className="border rounded p-3">
              <div className="font-mono text-xs text-muted-foreground">
                {a.decision_id}
              </div>
              <div className="text-sm">
                run <span className="font-mono">{a.run_id}</span> · policy{" "}
                {a.policy_id} · proposed{" "}
                <span className="font-mono">{action.kind ?? "?"}</span>
              </div>
              <details className="text-sm mt-2">
                <summary className="cursor-pointer">signal evidence</summary>
                <pre className="text-xs overflow-auto bg-muted p-2 rounded">
                  {JSON.stringify(a.triggering_signal, null, 2)}
                </pre>
              </details>
              <div className="flex gap-2 mt-2">
                <button
                  className="px-2 py-1 bg-green-600 text-white rounded disabled:opacity-50"
                  disabled={busy === a.decision_id}
                  onClick={() => decide(a.decision_id, "approve")}
                >
                  Approve
                </button>
                <button
                  className="px-2 py-1 bg-red-600 text-white rounded disabled:opacity-50"
                  disabled={busy === a.decision_id}
                  onClick={() => decide(a.decision_id, "deny")}
                >
                  Deny
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </main>
  );
}
