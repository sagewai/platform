'use client';

import { ServerCrash, RefreshCcw, Activity } from 'lucide-react';
import Link from 'next/link';
import { Button, buttonVariants } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';

interface SystemOfflineProps {
  onRetry?: () => void;
}

/**
 * User-friendly empty state shown when the SAGEWAI API is unreachable.
 * Replaces the old red banner with raw shell commands.
 */
export function SystemOffline({ onRetry }: SystemOfflineProps) {
  const handleRetry = () => {
    if (onRetry) onRetry();
    else if (typeof window !== 'undefined') window.location.reload();
  };

  return (
    <EmptyState
      icon={ServerCrash}
      title="Backend not reachable"
      description="We can't reach the SAGEWAI API right now. Check your connection or try again in a moment."
      action={
        <>
          <Button onClick={handleRetry}>
            <RefreshCcw className="mr-2 h-4 w-4" /> Retry
          </Button>
          <Link href="/system/health" className={buttonVariants({ variant: 'outline' })}>
            <Activity className="mr-2 h-4 w-4" /> View status
          </Link>
        </>
      }
    >
      <details className="mt-6 text-xs text-muted-foreground">
        <summary className="cursor-pointer hover:text-foreground transition-colors">
          Developer info
        </summary>
        <p className="mt-2 max-w-md">
          The API server is not responding. If you're running locally, start the
          backend with{' '}
          <code className="font-mono px-1 py-0.5 rounded bg-muted text-foreground">
            make admin-serve
          </code>{' '}
          (or <code className="font-mono px-1 py-0.5 rounded bg-muted text-foreground">make dev-all</code> to run backend + frontend together) and refresh.
        </p>
      </details>
    </EmptyState>
  );
}
