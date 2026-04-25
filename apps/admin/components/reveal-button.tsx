"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/utils/api";

interface Props {
  profileId: string;
  secretKey: string;
}

export function RevealButton({ profileId, secretKey }: Props) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Auto-mask after 10 seconds
  useEffect(() => {
    if (!revealed) return;
    const t = setTimeout(() => setRevealed(null), 10_000);
    return () => clearTimeout(t);
  }, [revealed]);

  async function onClick() {
    if (revealed) {
      setRevealed(null);
      return;
    }
    if (!confirm(`Show value of ${secretKey}? This will be audit-logged.`)) return;
    setBusy(true);
    setError(null);
    try {
      const { value } = await adminApi.revealSecret(profileId, secretKey);
      setRevealed(value);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <span className="font-mono text-xs">
        {revealed ?? "••••••••"}
      </span>
      <button
        onClick={onClick}
        disabled={busy}
        className="text-xs text-blue-600 hover:underline disabled:opacity-50"
      >
        {revealed ? "Hide" : "Reveal"}
      </button>
      {error && <span className="text-xs text-rose-600">{error}</span>}
    </span>
  );
}
