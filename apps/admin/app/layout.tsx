'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { SidebarProvider } from '@/components/ui/legacy';
import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppSidebarShell } from '@/components/app-sidebar-shell';
import { NavSidebar } from '@/components/nav-sidebar';
import { PageTransition } from '@/components/page-transition';
import { CommandPalette } from '@/components/command-palette';
import { ErrorBoundary } from '@/components/error-boundary';
import { WorkflowToastListener } from '@/components/workflow-toast-listener';
import { ConnectionError } from '@/components/connection-error';
import { ConnectionProvider, useConnection } from '@/utils/connection';
import { LicenseProvider } from '@/utils/license';
import { ProjectProvider } from '@/utils/project-context';
import { silentRefresh, isAuthenticated, authFetch } from '@/utils/auth';
import { fontVariables } from './fonts';
import './globals.css';
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

const AUTH_PATHS = ['/login', '/register', '/forgot-password'];
const FULLSCREEN_PATHS = ['/setup', '/tv'];

function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { state: connState, neverConnected } = useConnection();
  const isAuthPage = AUTH_PATHS.includes(pathname);
  const isFullscreen = FULLSCREEN_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'));

  // Gate: don't render authenticated content until auth is confirmed.
  const [authReady, setAuthReady] = useState(false);

  useEffect(() => {
    if (isAuthPage || isFullscreen) {
      setAuthReady(true);
      return;
    }
    // Don't attempt auth refresh if backend is unreachable
    if (connState === 'disconnected') return;

    // Always call silentRefresh on mount, even when an in-memory token
    // is already present. The browser's session cookie may have expired
    // or be missing (e.g. SAGEWAI_DEV_TRUST_LOCAL just restarted the
    // backend), and analyticsClient relies on the cookie for the auth
    // routes that don't read the in-memory Bearer token. Calling refresh
    // unconditionally guarantees both surfaces stay synchronised.
    silentRefresh().then(async (token) => {
      if (token) {
        setAuthReady(true);
        return;
      }
      // If we already had a stale in-memory token from a previous mount,
      // keep rendering optimistically while the user re-authenticates.
      if (isAuthenticated()) {
        setAuthReady(true);
        return;
      }
      const base =
        process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace(/\/admin$/, '') ?? '';
      if (base) {
        await authFetch(`${base}/api/v1/auth/logout`, {
          method: 'POST',
        }).catch(() => {});
      }
      router.replace('/login');
    });
  }, [pathname, isAuthPage, isFullscreen, router, connState]);

  // Show connection error for non-auth, non-fullscreen pages when backend is down
  if (!isAuthPage && !isFullscreen && connState === 'disconnected' && neverConnected) {
    return <ConnectionError />;
  }

  if (isAuthPage || isFullscreen) {
    return <>{children}</>;
  }

  if (!authReady) {
    // Show a proper loading state (not indefinitely — connection check will catch timeouts)
    if (connState === 'checking') {
      return (
        <div className="flex flex-col items-center justify-center min-h-screen gap-3">
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          <div className="text-text-muted text-sm">Connecting to server...</div>
        </div>
      );
    }
    if (connState === 'disconnected') {
      return <ConnectionError />;
    }
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-text-muted text-sm">Loading...</div>
      </div>
    );
  }

  return (
    <ProjectProvider>
    <LicenseProvider>
      <SidebarProvider>
        <AppSidebarShell sidebar={<NavSidebar />}>
          <ErrorBoundary>
            <PageTransition>{children}</PageTransition>
          </ErrorBoundary>
        </AppSidebarShell>
        <CommandPalette />
        <WorkflowToastListener />
      </SidebarProvider>
    </LicenseProvider>
    </ProjectProvider>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={cn(fontVariables, "font-sans", geist.variable)}>
      <head>
        <link rel="icon" href="/brand/favicon.ico" sizes="any" />
        <link rel="icon" type="image/svg+xml" href="/brand/sagewai_icon.svg" />
        <link rel="apple-touch-icon" href="/brand/sagewai_icon.webp" />
        <meta name="application-name" content="Sagewai Admin" />
        <meta property="og:title" content="Sagewai Admin" />
        <meta property="og:description" content="Sagewai is the autonomous agent platform: describe the goal, we design the agents, run them in production, and fine-tune local models so every run gets cheaper." />
        <meta property="og:image" content="/brand/sagewai_logo.webp" />
        {/* External script for CSP compliance — avoids unsafe-inline */}
        <script src="/theme-init.js" />
      </head>
      <body>
        <TooltipProvider delay={200}>
          <ConnectionProvider>
            <AppShell>{children}</AppShell>
          </ConnectionProvider>
        </TooltipProvider>
        <Toaster richColors closeButton position="bottom-right" />
      </body>
    </html>
  );
}
