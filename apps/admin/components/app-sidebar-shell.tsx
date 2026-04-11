'use client';

import React from 'react';
import { motion } from 'framer-motion';
import { useSidebar } from '@/components/ui/legacy';

/**
 * Sidebar shell that follows the GLOBAL theme.
 *
 * Earlier versions hardcoded `data-theme="dark"` and `bg-[#0A1628]` on the
 * sidebar so it was always dark even when the rest of the app was in light
 * mode. We now read the shadcn `--sidebar` / `--sidebar-foreground` /
 * `--sidebar-border` tokens (defined in app/globals.css), which are themselves
 * aliased onto the brand tokens that swap with [data-theme="dark"].
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
        <header className="sticky top-0 z-30 flex items-center gap-3 bg-sidebar text-sidebar-foreground border-b border-sidebar-border px-4 py-3 shrink-0">
          <button
            onClick={() => setExpanded(true)}
            aria-label="Open navigation menu"
            className="bg-transparent border-none text-sidebar-foreground/80 cursor-pointer p-1.5 -ml-1.5 rounded-md hover:bg-sidebar-accent transition-colors"
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

      {/* Sidebar — follows the global theme via --sidebar tokens */}
      <motion.aside
        initial={false}
        animate={{ width: mobile ? 260 : expanded ? 260 : 64 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        style={mobile ? undefined : { height: '100vh', position: 'sticky', top: 0 }}
        className={`bg-sidebar text-sidebar-foreground border-r border-sidebar-border flex flex-col shrink-0 overflow-hidden ${
          mobile
            ? `fixed inset-y-0 left-0 z-50 ${expanded ? 'translate-x-0' : '-translate-x-full'}`
            : ''
        }`}
      >
        {sidebar}
      </motion.aside>

      {/* Main content — also follows the global theme */}
      <main className="flex-1 min-w-0 bg-background text-foreground p-md md:p-xl overflow-x-hidden">
        {children}
      </main>
    </div>
  );
}
