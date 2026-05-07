'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { adminApi } from '@/utils/api';

interface AutopilotOperatorDirectionsProps {
  compositionPayload: object;
}

export function AutopilotOperatorDirections({
  compositionPayload,
}: AutopilotOperatorDirectionsProps) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDirections = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await adminApi.explainBlueprint(compositionPayload);
      setMarkdown(res.markdown);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load operator directions.');
    } finally {
      setLoading(false);
    }
  }, [compositionPayload]);

  useEffect(() => {
    fetchDirections();
  }, [fetchDirections]);

  /* Loading */
  if (loading && markdown === null) {
    return (
      <div className="animate-pulse space-y-2">
        <div className="h-4 bg-bg-subtle rounded w-3/4" />
        <div className="h-4 bg-bg-subtle rounded w-full" />
        <div className="h-4 bg-bg-subtle rounded w-5/6" />
        <div className="h-4 bg-bg-subtle rounded w-2/3" />
      </div>
    );
  }

  /* Error */
  if (error) {
    return (
      <div className="flex items-start gap-3 bg-error/5 border border-error/20 rounded-lg px-4 py-3">
        <AlertTriangle className="w-4 h-4 text-error shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-error m-0">Failed to load directions</p>
          <p className="text-xs text-text-muted m-0 mt-0.5 break-words">{error}</p>
        </div>
      </div>
    );
  }

  /* Empty */
  if (!markdown) {
    return (
      <div className="flex flex-col items-center gap-2 py-8 text-text-muted">
        <FileText size={28} className="opacity-40" />
        <p className="text-sm m-0">No directions available.</p>
      </div>
    );
  }

  /* Content */
  return (
    <div className="prose prose-sm max-w-none text-text-primary [&_h1]:text-text-primary [&_h2]:text-text-primary [&_h3]:text-text-secondary [&_code]:bg-bg-subtle [&_code]:px-1 [&_code]:rounded [&_code]:text-[0.8em] [&_pre]:bg-bg-subtle [&_pre]:border [&_pre]:border-border [&_pre]:rounded-lg [&_a]:text-primary">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
    </div>
  );
}
