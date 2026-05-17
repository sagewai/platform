'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Menu, X, Search } from 'lucide-react';
import { DocsSidebar } from '@/components/docs-sidebar';
import { DocsFooter } from '@/components/docs-footer';
import { ThemeToggle } from '@/components/theme-toggle';
import { SearchModal } from '@/components/search-modal';

/**
 * Client-side shell for /docs/* pages.
 *
 * Owns the mobile drawer state for the sidebar and renders:
 * - A top nav bar with the SAGEWAI logo (icon-only on mobile, full
 *   wordmark on sm+), a "Docs" pill, desktop nav links, and a mobile
 *   hamburger toggle.
 * - The sidebar as a static 256px column on lg+ screens, or as a fixed
 *   drawer with overlay on mobile when open.
 *
 * The sidebar auto-closes on route change so tapping a nav item on
 * mobile doesn't leave the drawer hanging open.
 */
export function DocsShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const pathname = usePathname();

  // Close drawer on route change.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Cmd+K / Ctrl+K opens search.
  const handleSearchKey = useCallback((e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      setSearchOpen((v) => !v);
    }
  }, []);

  useEffect(() => {
    window.addEventListener('keydown', handleSearchKey);
    return () => window.removeEventListener('keydown', handleSearchKey);
  }, [handleSearchKey]);

  // Lock body scroll while drawer is open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  return (
    <div className="min-h-screen bg-bg-page">
      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
      {/* Docs Navigation Bar — solid background at all times so the
          theme-keyed wordmark always sits on a matching surface. Do not make
          this translucent or scroll-reactive. See the navbar rule in
          sagewai/atelier:brand/docs/sagewai_branding.md § "Logo & Navbar". */}
      <nav className="sticky top-0 z-50 bg-bg-page border-b border-border">
        <div className="max-w-[90rem] mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            {/* Hamburger — mobile only */}
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-label={open ? 'Close navigation' : 'Open navigation'}
              aria-expanded={open}
              className="lg:hidden -ml-1 p-2 rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-subtle transition-colors"
            >
              {open ? <X size={20} /> : <Menu size={20} />}
            </button>

            <Link href="/" className="flex items-center gap-2 shrink-0 min-w-0">
              {/* Full wordmark logo on all breakpoints. Light/dark variants. */}
              <img
                src="/brand/sagewai_logo.svg"
                alt="Sagewai"
                className="h-8 w-auto block dark:hidden"
              />
              <img
                src="/brand/sagewai_logo_dark.svg"
                alt="Sagewai"
                className="h-8 w-auto hidden dark:block"
              />
              <span className="text-xs bg-primary-light text-primary px-2 py-0.5 rounded-full font-medium shrink-0">
                Docs
              </span>
            </Link>
          </div>

          <div className="flex items-center gap-2 sm:gap-4">
            {/* Search button */}
            <button
              type="button"
              onClick={() => setSearchOpen(true)}
              aria-label="Search documentation"
              className="flex items-center gap-2 text-sm text-text-muted bg-bg-subtle hover:bg-bg-subtle/80 border border-border rounded-lg px-3 py-1.5 transition-colors"
            >
              <Search size={14} />
              <span className="hidden sm:inline text-text-muted">Search…</span>
              <kbd className="hidden md:inline-flex items-center gap-0.5 text-[11px] text-text-muted font-mono">
                ⌘K
              </kbd>
            </button>

            <div className="hidden md:flex items-center gap-6 lg:gap-8">
              <Link
                href="/docs/getting-started"
                className="text-sm text-text-secondary hover:text-primary transition-colors"
              >
                Getting Started
              </Link>
              <Link
                href="/docs/api-reference/python-sdk"
                className="text-sm text-text-secondary hover:text-primary transition-colors"
              >
                API Reference
              </Link>
              <Link
                href="/docs/guides/first-agent"
                className="text-sm text-text-secondary hover:text-primary transition-colors"
              >
                Guides
              </Link>
              <a
                href="https://github.com/sagewai/platform"
                className="text-sm text-text-secondary hover:text-primary transition-colors"
                target="_blank"
                rel="noopener noreferrer"
              >
                GitHub
              </a>
              <ThemeToggle />
            </div>
          </div>
        </div>
      </nav>

      {/* Mobile drawer overlay */}
      {open && (
        <div
          className="fixed inset-0 top-16 z-40 bg-black/40 lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      <div className="flex max-w-[90rem] mx-auto">
        {/*
         * Sidebar — fixed drawer on mobile, static column on lg+.
         *
         * Width uses explicit arbitrary values (`max-w-[20rem]`) instead of
         * the named `max-w-xs` class. docs still depends on @sagewai/tokens
         * which declares --spacing-xs in its @theme block; in Tailwind 4 that
         * clobbers the default container scale so `max-w-xs` resolves to
         * `var(--spacing-xs) = 4px`, collapsing the drawer to invisible on
         * mobile. Arbitrary values bypass that collision entirely.
         */}
        <div
          className={`
            fixed inset-y-16 left-0 z-50 w-[min(85vw,20rem)] transform transition-transform duration-200 lg:static lg:inset-auto lg:w-auto lg:transform-none lg:translate-x-0
            ${open ? 'translate-x-0' : '-translate-x-full'}
            lg:block
          `}
        >
          <DocsSidebar />
        </div>

        <main className="flex-1 min-w-0 px-4 py-6 sm:px-6 sm:py-8 lg:px-16 lg:py-12">
          <article className="max-w-3xl">{children}</article>
        </main>
      </div>

      <DocsFooter />
    </div>
  );
}
