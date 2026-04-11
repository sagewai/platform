'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { isAuthenticated } from '@/utils/auth';
import { useRole } from '@/hooks/use-role';

const TABS = [
  { href: '/system/organization', label: 'Organization' },
  { href: '/system/models', label: 'AI Models' },
  { href: '/system/connectors', label: 'Connectors' },
  { href: '/system/infrastructure', label: 'Infrastructure' },
  { href: '/system/projects', label: 'Projects' },
  { href: '/system/billing', label: 'Billing' },
  { href: '/system/notifications', label: 'Notifications' },
  { href: '/system/health', label: 'System Health' },
];

export default function SystemLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { permissions } = useRole();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/login');
    } else if (!permissions.canManageSystem) {
      router.replace('/');
    } else {
      setReady(true);
    }
  }, [router, permissions]);

  if (!ready) return null;

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1">System Settings</h1>
      <p className="text-text-secondary text-sm mb-6">Configure your Sagewai instance</p>

      <div className="flex gap-1 border-b border-white/10 mb-6 overflow-x-auto">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            className={`px-4 py-2.5 text-sm no-underline transition-colors border-b-2 -mb-px whitespace-nowrap ${
              pathname === tab.href
                ? 'border-primary text-white font-semibold'
                : 'border-transparent text-text-secondary hover:text-white'
            }`}
          >
            {tab.label}
          </Link>
        ))}
      </div>

      {children}
    </div>
  );
}
