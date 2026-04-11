'use client';

import { useRole } from '@/hooks/use-role';
import { WelcomeBanner } from './welcome-banner';
import { GettingStarted } from './getting-started';
import { QuickActions } from './quick-actions';

export function DashboardClientWidgets() {
  const { role } = useRole();

  return (
    <>
      <WelcomeBanner />
      <GettingStarted />
      <QuickActions role={role} />
    </>
  );
}
