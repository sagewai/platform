'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { SidebarProvider, ToastProvider } from '@sagecurator/ui';
import { AppSidebarShell } from '@/components/app-sidebar-shell';
import { NavSidebar } from '@/components/nav-sidebar';
import { PageTransition } from '@/components/page-transition';
import { WorkflowToastListener } from '@/components/workflow-toast-listener';
import { ConnectionError } from '@/components/connection-error';
import { ConnectionProvider, useConnection } from '@/utils/connection';
import { LicenseProvider } from '@/utils/license';
import { silentRefresh, isAuthenticated, authFetch } from '@/utils/auth';
import { fontVariables } from './fonts';
import './globals.css';

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
    if (isAuthenticated()) {
      setAuthReady(true);
      return;
    }
    // Don't attempt auth refresh if backend is unreachable
    if (connState === 'disconnected') return;

    silentRefresh().then(async (token) => {
      if (token) {
        setAuthReady(true);
      } else {
        const base =
          process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace(/\/admin$/, '') ?? '';
        if (base) {
          await authFetch(`${base}/api/v1/auth/logout`, {
            method: 'POST',
          }).catch(() => {});
        }
        router.replace('/login');
      }
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
    <LicenseProvider>
      <SidebarProvider>
        <AppSidebarShell sidebar={<NavSidebar />}>
          <PageTransition>{children}</PageTransition>
        </AppSidebarShell>
        <WorkflowToastListener />
      </SidebarProvider>
    </LicenseProvider>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={fontVariables}>
      <head>
        <link rel="icon" href="/brand/favicon.ico" sizes="any" />
        <link rel="apple-touch-icon" href="/brand/logo-256.png" />
        <meta name="application-name" content="Sagewai Admin" />
        <meta property="og:title" content="Sagewai Admin" />
        <meta property="og:description" content="Agent Infrastructure You Own — manage agents, workflows, and AI infrastructure" />
        <meta property="og:image" content="/brand/logo-256.png" />
        {/* External script for CSP compliance — avoids unsafe-inline */}
        <script src="/theme-init.js" />
      </head>
      <body>
        <ToastProvider>
          <ConnectionProvider>
            <AppShell>{children}</AppShell>
          </ConnectionProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
