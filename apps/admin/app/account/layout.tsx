'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const TABS = [
  { href: '/account/profile', label: 'Profile & Password' },
  { href: '/account/tokens', label: 'API Tokens' },
  { href: '/account/notifications', label: 'Notifications' },
  { href: '/account/security', label: '2FA Security' },
];

export default function AccountLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1">My Account</h1>
      <p className="text-text-secondary text-sm mb-6">Manage your personal settings and security</p>

      <div className="flex gap-1 border-b border-white/10 mb-6">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            className={`px-4 py-2.5 text-sm no-underline transition-colors border-b-2 -mb-px ${
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
