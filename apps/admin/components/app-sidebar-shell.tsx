'use client';

import React from 'react';
import { useSidebar } from '@sagecurator/ui';

/**
 * Local SidebarShell override that fixes theming:
 * - Sidebar: always dark (data-theme="dark" scoped)
 * - Content: follows the global theme (light or dark via html[data-theme])
 */
export function AppSidebarShell({
  sidebar,
  children,
}: {
  sidebar: React.ReactNode;
  children: React.ReactNode;
}) {
  const { expanded, setExpanded, mobile } = useSidebar();

  return (
    <div className="flex min-h-screen" style={{ flexDirection: mobile ? 'column' : 'row' }}>
      {/* Mobile top bar */}
      {mobile && !expanded && (
        <header className="sticky top-0 z-30 flex items-center gap-3 bg-[#0A1628] px-4 py-3 shrink-0">
          <button
            onClick={() => setExpanded(true)}
            aria-label="Open navigation menu"
            className="bg-transparent border-none text-white/80 cursor-pointer p-1.5 -ml-1.5 rounded-md hover:bg-white/10 transition-colors"
          >
            <svg width="22" height="22" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <span
            className="text-sm font-bold font-[family-name:var(--font-heading)] tracking-wide bg-clip-text text-transparent"
            style={{ backgroundImage: 'var(--gradient-brand)' }}
          >
            SAGEWAI
          </span>
        </header>
      )}

      {/* Overlay for mobile */}
      {mobile && expanded && (
        <div
          className="fixed inset-0 bg-black/40 z-40"
          onClick={() => setExpanded(false)}
        />
      )}

      {/* Sidebar — always dark themed */}
      <aside
        data-theme="dark"
        style={mobile ? undefined : { height: '100vh', position: 'sticky', top: 0 }}
        className={`bg-[#0A1628] text-[#F0F4F8] flex flex-col overflow-y-auto transition-all duration-200 shrink-0 ${
          mobile
            ? `fixed inset-y-0 left-0 z-50 w-[260px] ${expanded ? 'translate-x-0' : '-translate-x-full'}`
            : expanded
              ? 'w-[260px]'
              : 'w-16'
        }`}
      >
        {sidebar}
      </aside>

      {/* Main content — follows global theme */}
      <main className="flex-1 min-w-0 bg-bg-page p-md md:p-xl overflow-x-hidden">
        {children}
      </main>
    </div>
  );
}
