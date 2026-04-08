'use client';

import { useEffect } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@sagecurator/ui';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[GlobalError]', error);
  }, [error]);

  return (
    <div className="min-h-[60vh] flex items-center justify-center p-md">
      <div className="w-full max-w-[28rem] text-center">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-error/10 mb-lg">
          <AlertTriangle className="w-7 h-7 text-error" />
        </div>

        <h1 className="text-xl font-bold font-[family-name:var(--font-heading)] text-text-primary mb-2">
          Something went wrong
        </h1>
        <p className="text-sm text-text-secondary mb-lg">
          An unexpected error occurred while loading this page. This is usually
          caused by the backend being temporarily unavailable.
        </p>

        {error.message && (
          <div className="bg-bg-subtle rounded-lg px-4 py-3 mb-lg text-left">
            <code className="text-xs font-[family-name:var(--font-mono)] text-text-muted break-all">
              {error.message}
            </code>
          </div>
        )}

        <Button onClick={reset}>
          <RefreshCw className="w-4 h-4 mr-2" />
          Try Again
        </Button>
      </div>
    </div>
  );
}
