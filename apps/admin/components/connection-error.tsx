'use client';

import { useConnection } from '@/utils/connection';
import { WifiOff, RefreshCw, Terminal, Server } from 'lucide-react';
import { Button } from '@/components/ui/legacy';

/**
 * Full-page error state shown when the admin backend is unreachable.
 * Provides clear diagnostics and actionable next steps.
 */
export function ConnectionError() {
  const { state, retry } = useConnection();
  const isRetrying = state === 'checking';

  const apiUrl = process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace(/\/admin$/, '') ?? 'http://localhost:8000';

  return (
    <div className="min-h-screen bg-bg-deep flex items-center justify-center p-md">
      <div className="w-full max-w-[28rem] text-center">
        {/* Icon */}
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-error/10 mb-lg">
          <WifiOff className="w-8 h-8 text-error" />
        </div>

        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] text-text-on-dark mb-2">
          Cannot Connect to Backend
        </h1>
        <p className="text-sm text-text-secondary mb-xl">
          The admin panel cannot reach the API server at{' '}
          <code className="text-xs font-[family-name:var(--font-mono)] px-1.5 py-0.5 rounded">
            {apiUrl}
          </code>
        </p>

        {/* Diagnostics */}
        <div className="bg-surface-dark rounded-lg border border-border-dark text-left p-lg mb-lg">
          <h3 className="text-sm font-semibold text-text-on-dark mb-md flex items-center gap-2">
            <Terminal className="w-4 h-4 text-text-muted" />
            Troubleshooting
          </h3>
          <ol className="text-sm text-text-secondary space-y-3 list-none p-0 m-0">
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/10 text-primary text-xs font-bold flex items-center justify-center">1</span>
              <div>
                <strong className="text-text-on-dark">Start the backend server</strong>
                <code className="block text-xs font-[family-name:var(--font-mono)] bg-bg-deep px-2 py-1.5 rounded mt-1 border border-border-dark">
                  make dev-native APP=admin
                </code>
              </div>
            </li>
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/10 text-primary text-xs font-bold flex items-center justify-center">2</span>
              <div>
                <strong className="text-text-on-dark">Ensure the database is running</strong>
                <code className="block text-xs font-[family-name:var(--font-mono)] bg-bg-deep px-2 py-1.5 rounded mt-1 border border-border-dark">
                  make dev-lite && make db-upgrade
                </code>
              </div>
            </li>
            <li className="flex gap-3">
              <span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/10 text-primary text-xs font-bold flex items-center justify-center">3</span>
              <div>
                <strong className="text-text-on-dark">Verify the API URL</strong>
                <span className="block text-xs text-text-muted mt-0.5">
                  Check that <code className="font-[family-name:var(--font-mono)]">NEXT_PUBLIC_ADMIN_API_URL</code> in{' '}
                  <code className="font-[family-name:var(--font-mono)]">.env.local</code> points to the correct server.
                </span>
              </div>
            </li>
          </ol>
        </div>

        {/* Retry button */}
        <Button onClick={retry} disabled={isRetrying} className="min-w-[160px]">
          <RefreshCw className={`w-4 h-4 mr-2 ${isRetrying ? 'animate-spin' : ''}`} />
          {isRetrying ? 'Connecting...' : 'Retry Connection'}
        </Button>

        {/* Status indicator */}
        <div className="flex items-center justify-center gap-2 mt-lg text-xs text-text-muted">
          <Server className="w-3 h-3" />
          <span>Auto-retrying every 8 seconds</span>
          <span className={`w-2 h-2 rounded-full ${isRetrying ? 'bg-warning animate-pulse' : 'bg-error'}`} />
        </div>
      </div>
    </div>
  );
}
