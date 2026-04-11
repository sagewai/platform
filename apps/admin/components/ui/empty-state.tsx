import * as React from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  children?: React.ReactNode;
}

/**
 * Project-local EmptyState wrapper. Built from Card-style tokens so it
 * matches the rest of the UI in both themes.
 *
 * Uses a plain block layout (NOT flex-col items-center) because
 * `items-center` collapses children to min-content width, which wraps text
 * one word per line. With a block layout, each child renders at full
 * container width and `text-center` handles alignment.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
  children,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'w-full rounded-xl border border-border bg-card text-card-foreground px-6 py-12 text-center',
        className,
      )}
    >
      {Icon && (
        <div className="mx-auto mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
          <Icon className="h-6 w-6" aria-hidden="true" />
        </div>
      )}
      <h3 className="text-base font-semibold m-0 mb-1">{title}</h3>
      {description && (
        <p className="mx-auto max-w-xl text-sm text-muted-foreground m-0">{description}</p>
      )}
      {children && <div className="mt-4 mx-auto max-w-xl">{children}</div>}
      {action && (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {action}
        </div>
      )}
    </div>
  );
}
