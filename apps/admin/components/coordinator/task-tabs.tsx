'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

const TABS = [
  { segment: '', label: 'Thread' },
  { segment: '/plan', label: 'Plan' },
  { segment: '/actions', label: 'Actions' },
  { segment: '/activity', label: 'Activity' },
  { segment: '/telemetry', label: 'Telemetry' },
  { segment: '/settings', label: 'Settings' },
] as const;

export function TaskTabs({ taskId }: { taskId: string }) {
  const pathname = usePathname();
  const base = `/tasks/${encodeURIComponent(taskId)}`;
  return (
    <nav className="flex gap-1 border-b border-border" aria-label="Task views">
      {TABS.map((tab) => {
        const href = `${base}${tab.segment}`;
        const active = pathname === href;
        return (
          <Link
            key={tab.label}
            href={href}
            aria-current={active ? 'page' : undefined}
            className={cn(
              '-mb-px inline-flex items-center border-b-2 px-4 py-2 text-sm no-underline transition-colors',
              active
                ? 'border-primary font-medium text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
