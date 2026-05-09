'use client';

import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';

import { adminApi } from '@/utils/api';

export function AutopilotDirections({ missionId }: { missionId: string }) {
  const [md, setMd] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMd(null);
    setError(null);
    adminApi
      .explainAutopilotMission(missionId)
      .then((r) => {
        if (!cancelled) setMd(r.markdown);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [missionId]);

  if (error) {
    return (
      <p className="text-sm text-text-secondary m-0" data-testid="directions-error">
        Could not load brief: {error}
      </p>
    );
  }

  if (md === null) {
    return (
      <div data-testid="directions-skeleton" className="space-y-2">
        <div className="h-4 w-1/3 rounded bg-bg-subtle animate-pulse" />
        <div className="h-3 w-3/4 rounded bg-bg-subtle animate-pulse" />
        <div className="h-3 w-2/3 rounded bg-bg-subtle animate-pulse" />
      </div>
    );
  }

  return (
    <div
      className="prose prose-sm max-w-none text-text-primary dark:prose-invert"
      data-testid="directions-markdown"
    >
      <ReactMarkdown>{md}</ReactMarkdown>
    </div>
  );
}
