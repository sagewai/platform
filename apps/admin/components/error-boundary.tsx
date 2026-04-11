'use client';

import React from 'react';
import { AlertTriangle, RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';

interface State {
  hasError: boolean;
  error?: Error;
}

/**
 * Top-level error boundary used inside the root layout. Catches render errors
 * thrown by any child page and renders a friendly recovery UI instead of the
 * Next.js default error overlay.
 */
export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  reset = () => {
    this.setState({ hasError: false, error: undefined });
    if (typeof window !== 'undefined') window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="min-h-[60vh] flex items-center justify-center p-6">
        <EmptyState
          icon={AlertTriangle}
          title="Something went wrong"
          description={
            this.state.error?.message ||
            'An unexpected error occurred while rendering this page.'
          }
          action={
            <Button onClick={this.reset} variant="default">
              <RefreshCcw className="mr-2 h-4 w-4" /> Reload page
            </Button>
          }
        />
      </div>
    );
  }
}
