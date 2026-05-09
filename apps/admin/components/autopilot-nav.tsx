'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

const TABS = [
  { href: '/autopilot', label: 'Goals' },
  { href: '/autopilot/missions', label: 'Missions' },
  { href: '/autopilot/orchestration', label: 'Orchestration' },
] as const;

export function AutopilotNavTabs({ runningCount }: { runningCount?: number }) {
  const pathname = usePathname();
  return (
    <nav className="flex gap-1 border-b border-border mb-6" role="tablist" aria-label="Autopilot">
      {TABS.map((t) => {
        const active =
          pathname === t.href ||
          (t.href !== '/autopilot' && pathname.startsWith(t.href));
        return (
          <Link
            key={t.href}
            href={t.href}
            role="tab"
            aria-selected={active}
            className={cn(
              'px-4 py-2 text-sm -mb-px transition-colors inline-flex items-center gap-1.5',
              active
                ? 'border-b-2 border-accent text-text-primary font-medium'
                : 'text-text-secondary hover:text-text-primary border-b-2 border-transparent',
            )}
          >
            {t.label}
            {t.label === 'Missions' && runningCount ? (
              <span className="inline-flex items-center justify-center min-w-5 h-5 px-1 rounded-full bg-warning/10 text-warning text-xs font-semibold">
                {runningCount}
              </span>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}

export function AutopilotBreadcrumbs({
  trail,
}: {
  trail: { label: string; href?: string }[];
}) {
  return (
    <ol
      className="flex gap-1 text-sm text-text-secondary mb-4"
      aria-label="Breadcrumb"
    >
      {trail.map((c, i) => (
        <li key={i} className="flex items-center gap-1">
          {c.href ? (
            <Link href={c.href} className="hover:text-text-primary transition-colors">
              {c.label}
            </Link>
          ) : (
            <span className="text-text-primary">{c.label}</span>
          )}
          {i < trail.length - 1 && (
            <span aria-hidden className="text-text-muted">
              ›
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
