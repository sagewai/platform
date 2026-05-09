import type { ReactNode } from 'react';
import { AutopilotNavTabs } from '@/components/autopilot-nav';

export default function AutopilotLayout({ children }: { children: ReactNode }) {
  return (
    <div className="max-w-4xl mx-auto">
      <AutopilotNavTabs />
      {children}
    </div>
  );
}
