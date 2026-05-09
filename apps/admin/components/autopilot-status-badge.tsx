import { cn } from '@/lib/utils';

const VARIANTS: Record<string, string> = {
  pending: 'bg-bg-subtle text-text-secondary',
  draft: 'bg-bg-subtle text-text-muted',
  approved: 'bg-primary/10 text-primary',
  scheduled: 'bg-secondary/10 text-secondary',
  running: 'bg-warning/10 text-warning animate-pulse',
  completed: 'bg-success/10 text-success',
  failed: 'bg-error/10 text-error',
  cancelled: 'bg-text-muted/10 text-text-muted',
};

export type MissionStatus = keyof typeof VARIANTS;

export function AutopilotStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide',
        VARIANTS[status] ?? 'bg-bg-subtle text-text-muted',
      )}
    >
      {status}
    </span>
  );
}
